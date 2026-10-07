"""Checkouts: worktrees, sandboxes, git and what a change measures.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_writes_cannot_escape_the_repo(tmp_path):
    (tmp_path / "inside.txt").write_text("ok")
    assert workspace.safe_join(tmp_path, "a/b/c.py").is_relative_to(tmp_path)
    for escape in ("../outside.py", "/etc/passwd", "a/../../outside.py"):
        with pytest.raises(workspace.PathEscape):
            workspace.safe_join(tmp_path, escape)


def test_spec_hash_tracks_the_contract_not_the_prose():
    spec = Spec(title="t", intent="i", summary="s",
                acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="a thing")])
    before = workspace.spec_hash(spec)
    assert workspace.spec_hash(spec.model_copy(deep=True)) == before

    changed = spec.model_copy(deep=True)
    changed.acceptance_criteria[0].statement = "a different thing"
    assert workspace.spec_hash(changed) != before


def test_the_digest_reports_how_much_it_could_not_carry(tmp_path):
    """"The agent misunderstood the codebase" and "the agent was shown 8% of the
    codebase" look identical from the outside. Only one is the agent's fault."""
    from factory.workspace import digest_coverage

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    for i in range(12):
        (repo / "pkg" / f"mod_{i}.py").write_text("x = 1\n" * 500)

    generous = digest_coverage(repo, 10_000_000)
    assert generous["covered"] == 1.0
    assert generous["files"] == 12

    starved = digest_coverage(repo, 1_000)
    assert starved["covered"] < 0.05, "a budget far under the source size reports as such"
    assert starved["chars"] > starved["budget"]


def test_slicing_covers_everything_a_single_digest_would_truncate(tmp_path):
    from factory.workspace import digest_coverage, slice_coverage, slice_repo

    repo = _repo_of(tmp_path, {f"pkg{i}/mod_{j}.py": 30_000 for i in range(4) for j in range(4)})

    # one truncated pass sees a fraction and reports like it saw everything
    single = digest_coverage(repo, 120_000)
    assert single["covered"] < 0.3

    kept, dropped = slice_repo(repo, 120_000, 16)
    coverage = slice_coverage(kept, dropped, repo)
    assert coverage["covered"] == 1.0
    assert coverage["files_read"] == coverage["files"] == 16
    assert not dropped


def test_slices_pack_rather_than_wasting_a_call_on_one_small_file(tmp_path):
    from factory.workspace import slice_repo

    repo = _repo_of(tmp_path, {
        "big/a.py": 100_000, "big/b.py": 100_000,
        "tiny/one.py": 200, "tiny/two.py": 200, "other/three.py": 300,
    })
    kept, _ = slice_repo(repo, 120_000, 16)
    assert all(sl.paths for sl in kept)
    small = [sl for sl in kept if sl.chars < 5_000]
    assert len(small) <= 1, "the small files share a slice instead of each taking a call"


def test_a_dropped_slice_is_named_not_swallowed(tmp_path):
    from factory.workspace import slice_coverage, slice_repo

    # Each file is capped at SLICE_FILE_CHARS, so it takes many of them to
    # exceed two slices' worth of budget.
    repo = _repo_of(tmp_path, {f"area{i}/mod_{j}.py": 30_000 for i in range(6) for j in range(4)})
    kept, dropped = slice_repo(repo, 120_000, 2)

    assert len(kept) == 2, "the cap is honoured"
    assert dropped, "and what did not fit is handed back rather than discarded"

    coverage = slice_coverage(kept, dropped, repo)
    assert coverage["covered"] < 0.5
    assert coverage["slices_dropped"] == len(dropped)
    assert coverage["files_read"] < coverage["files"]
    assert all(sl.name for sl in dropped), "every dropped slice can be named in the packet"


# ==========================================================================
# reading a repository that is mostly not code
# ==========================================================================


def test_gitignored_files_are_not_read(tmp_path):
    """A real project keeps far more on disk than it tracks. On one repository
    this was the difference between walking 750 million characters and reading
    the 11 million that are the codebase."""
    import subprocess

    from factory.workspace import iter_repo_files

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "logs").mkdir()
    (repo / "src" / "app.py").write_text("def go(): ...\n")
    (repo / "logs" / "huge.log").write_text("noise\n" * 50_000)
    (repo / "secrets.env").write_text("KEY=1\n")
    (repo / ".gitignore").write_text("logs/\n*.env\n")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    read = {str(p.relative_to(repo)) for p in iter_repo_files(repo)}
    assert "src/app.py" in read
    assert not any(r.startswith("logs/") for r in read), "gitignored directory was read"
    assert "secrets.env" not in read, "gitignored file was read"


def test_untracked_but_unignored_files_are_still_read(tmp_path):
    """Work in progress has to be visible, or the scout reads a stale repo."""
    import subprocess

    from factory.workspace import iter_repo_files

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("ignored.py\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "brand_new.py").write_text("x = 1\n")
    (repo / "ignored.py").write_text("x = 1\n")

    read = {str(p.relative_to(repo)) for p in iter_repo_files(repo)}
    assert "brand_new.py" in read
    assert "ignored.py" not in read


def test_the_index_answers_what_a_slice_cannot(tmp_path):
    """Slicing costs one thing: no scout sees the whole repo, so none can know a
    name is defined twice. That was never a judgment -- it is a lookup."""
    from factory.workspace import symbol_index

    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "backend" / "models.py").write_text(
        "class OutreachStatus:\n    pass\n\ndef parse_window(text):\n    pass\n"
    )
    (repo / "frontend" / "types.ts").write_text(
        "export interface OutreachStatus { a: string }\nexport const helper = () => 1\n"
    )

    index = symbol_index(repo)
    assert set(index.duplicates) == {"OutreachStatus"}, \
        "a name defined on both sides of the stack is the finding no slice can make"
    assert sorted(index.duplicates["OutreachStatus"]) == ["backend/models.py", "frontend/types.ts"]

    rendered = index.render(60_000)
    assert "OutreachStatus" in rendered
    assert "parse_window" in rendered
    assert "Defined in more than one file (1)" in rendered


def test_the_index_ignores_test_functions(tmp_path):
    """A test function is never something you would accidentally reimplement,
    and counting them drowns the signal: 400 `def test_*` outrank the module."""
    from factory.workspace import symbol_index

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "engine.py").write_text("def run_engine(): ...\n")
    (repo / "tests" / "test_engine.py").write_text(
        "".join(f"def test_case_{i}(): ...\n" for i in range(50))
    )

    index = symbol_index(repo)
    assert "run_engine" in index.by_name
    assert not any(n.startswith("test_case") for n in index.by_name)
    assert list(index.by_file) == ["src/engine.py"]


def test_selection_follows_the_intent_not_the_alphabet(tmp_path):
    """On a repository too big to read entirely, this is the difference between
    reading the part that bears on the intent and reading whatever sorts first."""
    from factory.workspace import slice_repo, symbol_index

    repo = tmp_path / "repo"
    for area in ("aaa_billing", "zzz_scheduling"):
        (repo / area).mkdir(parents=True)
        for i in range(6):
            (repo / area / f"mod_{i}.py").write_text("x = 1\n" * 4_000)
    (repo / "zzz_scheduling" / "slots.py").write_text(
        "class ScreeningSlot:\n    pass\n\ndef availability_window(): ...\n"
    )

    index = symbol_index(repo)
    kept, dropped = slice_repo(
        repo, 60_000, 2, focus="track screening slot availability windows", index=index,
    )
    chosen = " ".join(sl.name for sl in kept)
    assert "zzz_scheduling" in chosen, (
        "the slice defining ScreeningSlot must outrank the one that merely sorts first"
    )
    assert dropped, "and what was not read is handed back to be named"


def test_setup_the_factory_asked_for_is_seen_by_the_next_reading(tmp_path):
    """A name table cannot decide what counts as test setup.

    `_SETUP_NAMES` is a guess at what such a file tends to be called, and it
    missed one in the worst possible way: the factory proposed
    `frontend/src/test-utils.tsx` as scaffolding, a human wrote it, and the next
    reading could not see it -- so it reported that tier as having nothing to
    reuse, and described as missing the exact helper sitting in front of it.

    The fix is provenance, not more names. Anything a human applied as
    scaffolding, or that the last approved reading recorded as setup, is setup.
    """
    from factory.workspace import testing_paths

    repo = tmp_path / "repo"
    (repo / "frontend" / "src").mkdir(parents=True)
    (repo / "backend").mkdir()
    (repo / "backend" / "conftest.py").write_text("import pytest\n")
    (repo / "frontend" / "src" / "test-utils.tsx").write_text("export const render = 1\n")

    plain = testing_paths(repo)
    assert "backend/conftest.py" in plain, "the name table stopped working entirely"
    assert "frontend/src/test-utils.tsx" not in plain, (
        "this test proves nothing unless the name table really does miss this file")

    told = testing_paths(repo, known=["frontend/src/test-utils.tsx"])
    assert "frontend/src/test-utils.tsx" in told
    assert "backend/conftest.py" in told, "naming one file hid the ones found by name"
    assert len(told) == len(set(told)), "a file named by both routes was listed twice"

    # A path that no longer exists is not carried into a prompt as though it did.
    assert "gone/helper.ts" not in testing_paths(repo, known=["gone/helper.ts"])

    src = inspect.getsource(
        __import__("factory.onboarding", fromlist=["x"]).ProjectOnboarding.run_resurvey)
    assert "project.state.scaffolding_applied" in src and "tier.setup_files" in src, \
        "the reading is not told what this project already established as setup"


def test_a_new_linter_is_noticed_and_ordinary_commits_are_not(tmp_path):
    """The druff case. You cannot enumerate the tools, so watch for the shape of
    tooling changing -- a config file, a manifest entry, a CI step."""
    from factory.workspace import head_sha, tooling_drift

    repo = _git_repo(tmp_path / "repo")
    surveyed_at = head_sha(repo)

    # Ordinary work. Nothing about how the project is verified has moved.
    (repo / "src" / "b.py").write_text("y = 2\n")
    (repo / "src" / "a.py").write_text("x = 99\n")
    _commit(repo, "feature work")
    quiet = tooling_drift(repo, surveyed_at)
    assert quiet["checked"] and quiet["commits"] == 1
    assert not quiet["stale"], f"source commits must not trip the detector: {quiet}"

    # A DSL arrives with its own linter, announcing itself the way tools do.
    (repo / "druff.toml").write_text("[druff]\nstrict = true\n")
    _commit(repo, "add druff")
    loud = tooling_drift(repo, surveyed_at)
    assert loud["stale"] and "druff.toml" in loud["added"]


def test_a_tooling_file_the_survey_never_read_is_reported(tmp_path):
    """The digest is bounded. A surveyor that was never shown `druff.toml` did
    not decline to gate it, and that is a different failure from drift."""
    from factory.workspace import head_sha, tooling_drift, tooling_paths

    repo = _git_repo(tmp_path / "repo")
    (repo / "druff.toml").write_text("[druff]\n")
    _commit(repo, "add druff")

    seen = [p for p in tooling_paths(repo) if p != "druff.toml"]
    drift = tooling_drift(repo, head_sha(repo), known_paths=seen)
    assert drift["commits"] == 0, "nothing has moved since"
    assert drift["unseen"] == ["druff.toml"] and drift["stale"]


def test_a_survey_with_no_recorded_commit_says_so_rather_than_claiming_fresh(tmp_path):
    from factory.workspace import tooling_drift

    repo = _git_repo(tmp_path / "repo")
    drift = tooling_drift(repo, "")
    assert not drift["checked"] and not drift["stale"]
    assert "did not record" in drift["reason"]

    unknown = tooling_drift(repo, "deadbeef" * 5)
    assert not unknown["checked"] and "not in this repository" in unknown["reason"]


def test_the_ci_workflow_is_readable(tmp_path):
    """The surveyor is told CI is the strongest evidence a repository offers,
    and `.github` was in SKIP_DIRS -- so on every project it ever read, it
    inferred gates while the authoritative answer sat in a directory the walker
    dropped. Skipping a directory because most of it is noise threw away the
    one part that was not."""
    from factory.workspace import iter_repo_files
    import subprocess

    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "ISSUE_TEMPLATE").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (repo / ".github" / "ISSUE_TEMPLATE" / "bug.md").write_text("# bug\n")
    (repo / ".github" / "FUNDING.yml").write_text("github: [x]\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("//\n")

    def listed() -> set[str]:
        return {p.relative_to(repo).as_posix() for p in iter_repo_files(repo)}

    # Without git, the os.walk path.
    got = listed()
    assert ".github/workflows/ci.yml" in got, "the one file the surveyor most needs"
    assert ".github/ISSUE_TEMPLATE/bug.md" not in got, "boilerplate stays out"
    assert ".github/FUNDING.yml" not in got
    assert "node_modules/dep.js" not in got, "the rest of the skip list still holds"
    assert "src/a.py" in got

    # And with git, which is the path a real project takes.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    got = listed()
    assert ".github/workflows/ci.yml" in got
    assert ".github/ISSUE_TEMPLATE/bug.md" not in got
    assert "node_modules/dep.js" not in got


def test_a_repository_and_a_repository_with_a_commit_are_different_questions(tmp_path):
    """Two functions were both called `git_available`. One asked whether a
    directory is a repository, the other whether it has a commit -- and a
    repository just made by `git init` is the first and not the second. Both
    questions are real, so both are asked by name, from one module."""
    import subprocess

    from factory import git

    assert not git.is_repo(tmp_path) and not git.has_commit(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert git.is_repo(tmp_path), "a fresh repository is a repository"
    assert not git.has_commit(tmp_path), "and has nothing a worktree can be made from"
    assert git.head_sha(tmp_path) == "" and git.out(["rev-parse", "HEAD"], tmp_path) is None
    (tmp_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a"],
                   cwd=tmp_path, check=True)
    assert git.has_commit(tmp_path) and len(git.head_sha(tmp_path)) == 40
    assert git.current_branch(tmp_path)
    assert git.run(["no-such-command"], tmp_path).returncode != 0, "a failure is the caller's to read"


def test_every_report_the_factory_names_stays_off_the_branch(tmp_path):
    from factory.workspace import NOT_A_REPORT

    assert NOT_A_REPORT.endswith("*"), "only one kind of report is kept off the branch"
    repo = _repo_with_one_commit(tmp_path / "repo", {"a.py": "x = 1\n"})
    (repo / ".factory-report-1a2b3c4d.sarif").write_text("{}")
    assert gates.sweep_reports(repo) == [".factory-report-1a2b3c4d.sarif"]


def test_runner_chatter_is_not_recorded_as_an_installed_package():
    """A probe runs through gate machinery, so its output carries the runner's.

    The first version took the leading word of every line and recorded
    `Container` as an installed package -- harmless, wrong, and the kind of
    wrong that becomes load-bearing once something starts trusting the list.
    """
    sample = (
        " Container factory-x-postgres-1 Running \n"
        " Container factory-x-postgres-1 Healthy \n"
        "pip==25.0.1\n"
        "SQLAlchemy==2.0.35\n"
        "asyncpg==0.29.0\n"
        "/app\n"
        "└── (empty)\n"
        "├── @types/node@20.1.0\n"
        "├── vite@5.4.21\n"
        "npm notice New major version of npm available!\n"
    )
    names = workspace.installable_names(workspace.parse_package_lines(sample))

    assert "Container" not in names, "the runner's own output became a package"
    assert not any(n.startswith("/") for n in names)
    assert "(empty)" not in names

    assert "asyncpg==0.29.0" in names and "SQLAlchemy==2.0.35" in names
    # npm writes name@version; pip writes name==version. One spelling downstream.
    assert "vite==5.4.21" in names, "an npm entry was dropped or left unnormalised"
    assert "@types/node==20.1.0" in names, "a scoped name split at the wrong @"


# --------------------------------------------------------------------------
# INV-1 as a filesystem fact, and the ceiling that killed three runs
#
# The oracle and the breaker were the last two agents returning file text
# inside a completion. Three consecutive breaker rounds died at the ceiling
# (113,364 / 179,995 / 267,437 characters) and three consecutive oracle runs
# shipped a suite with a bug in it that a `--collect-only` would have caught.
# Both now write files through the harness; the files are read off disk and the
# model is asked only for what disk cannot answer.
# --------------------------------------------------------------------------


def test_a_blank_tree_contains_nothing_and_says_so():
    from factory.workspace import BlankTree, WorktreeError

    with BlankTree(label="oracle-x") as tree:
        assert tree.path is not None
        tree.assert_blank()          # no exception: this is the whole point
        # It is a repository, because the harness reads `git status` to decide
        # whether the agent wrote anything.
        assert (tree.path / ".git").is_dir()
        (tree.path / "leaked.py").write_text("the implementation", encoding="utf-8")
        with pytest.raises(WorktreeError) as caught:
            tree.assert_blank()
        assert "leaked.py" in str(caught.value)
        assert "must not see" in str(caught.value)


def test_the_blank_tree_is_removed_with_its_parent():
    from factory.workspace import BlankTree

    with BlankTree() as tree:
        path = tree.path
    assert path is not None and not path.exists()


def test_a_base_tree_is_the_base_commit_or_it_refuses():
    """The check is the point. A tree that is quietly the branch verifies
    nothing and reports that it did."""
    import os as _os
    import subprocess as _sp
    from factory.workspace import BaseTree, WorktreeError

    repo = Path(tempfile.mkdtemp()) / "r"
    repo.mkdir(parents=True)
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*a):
        return _sp.run(["git", *a], cwd=repo, capture_output=True, text=True, env=env)

    git("init", "--quiet")
    (repo / "README.md").write_text("docs that predate the work\n")
    (repo / "schema.sql").write_text("CREATE TABLE tenants (slug TEXT, display_name TEXT);\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text("def test_old(): pass\n")
    git("add", "-A"); git("commit", "--quiet", "-m", "before")
    base = git("rev-parse", "HEAD").stdout.strip()
    (repo / "feature.py").write_text("# THE IMPLEMENTATION\n")
    git("add", "-A"); git("commit", "--quiet", "-m", "the feature")

    with BaseTree(repo, base, label="t") as tree:
        tree.assert_at_base()
        assert tree.path is not None
        # Everything that was there before -- including the documentation and
        # the existing tests, which is the whole change.
        assert (tree.path / "schema.sql").read_text().strip().endswith("display_name TEXT);")
        assert (tree.path / "README.md").exists()
        assert (tree.path / "tests" / "test_old.py").exists()
        # And none of the work.
        assert not (tree.path / "feature.py").exists(), "the oracle can read the implementation"

    head = git("rev-parse", "HEAD").stdout.strip()
    with BaseTree(repo, base, label="t2") as tree:
        tree.base_sha = head                      # as if it had drifted to the branch
        with pytest.raises(WorktreeError) as caught:
            tree.assert_at_base()
        assert "verifies nothing" in str(caught.value)

    with pytest.raises(WorktreeError) as caught:
        with BaseTree(repo, "", label="t3"):
            pass
    assert "branched from" in str(caught.value)


def test_import_examples_are_safe_by_shape_not_by_hope():
    """`verify_context` takes prose only where provenance vouches for it, and
    filters everything else by shape. This field is handed to a model and will
    be handed a whole test file eventually, so it gets the same treatment as
    `installable_names`: an import line survives and nothing else does."""
    kept = workspace.import_lines([
        "from app.config import settings",
        "import { Wordmark } from './Wordmark'",
        "The components use named exports throughout.",
        "def test_thing(): assert compute(3) == 9",
        "x" * 400,
    ])
    assert kept == ["from app.config import settings",
                    "import { Wordmark } from './Wordmark'"]

    # A whole file pasted in leaks its imports and nothing else -- which is the
    # realistic mistake, and the one the shape filter has to survive.
    leaked = workspace.import_lines([
        "from app.models import Trial\n"
        "def test_secret():\n"
        "    assert Trial.margin == 0.4"
    ])
    assert leaked == ["from app.models import Trial"]

    # An import is itself often several lines, and splitting one on newlines
    # hands over `from x import (` as the example to follow. A real resurvey
    # proposed exactly that entry, so brackets are counted rather than ignored.
    assert workspace.import_lines([
        "from app.services.outreach import (\n    hash_contact,\n    tenant_key,\n)"
    ]) == ["from app.services.outreach import ( hash_contact, tenant_key, )"]
    assert workspace.import_lines([
        "import {\n  render,\n  screen,\n} from '@testing-library/react'"
    ]) == ["import { render, screen, } from '@testing-library/react'"]
    # A bracket that never closes is dropped, not passed on half-written.
    assert workspace.import_lines(["from app.x import (\n    a,"]) == []


def test_a_stale_breaker_checkout_is_declared_not_hidden(tmp_path):
    """The breaker's tree comes from the sandbox's last commit. Uncommitted work
    is not in it, so a probe written against that tree is a fact about code
    nobody is running -- and it would otherwise read as the feature failing."""
    from factory.workspace import uncommitted_paths

    repo = _blank_repo(tmp_path, "sandbox")
    assert uncommitted_paths(repo) == []
    (repo / "queue.py").write_text("repaired\n", encoding="utf-8")
    assert uncommitted_paths(repo) == ["queue.py"]

    src = inspect.getsource(pipeline.Factory._author_probes)
    assert "uncommitted = uncommitted_paths(sandbox.path)" in src
    assert "provisional" in src, \
        "a stale checkout must reach the packet, not just the log"


def test_a_kept_probe_is_committed_rather_than_merely_left_on_disk():
    """A file that is only *not deleted* is invisible.

    The packet's own commit is `commit_file` on one document, a final round
    that ends without a repair commits nothing at all, and a reverted round's
    `reset_to` would take an uncommitted probe with it. So promotion has to
    put the probe on the branch itself, or "kept" means kept until the next
    thing that touches git.

    With a pathspec. This runs between commits rather than after the last one,
    and an `add -A` here would put whatever else is mid-flight in the tree onto
    the branch under a message about probes.
    """
    src = inspect.getsource(pipeline.Factory._run_breaker)
    assert "sandbox.commit_files(" in src, "a kept probe has to reach the branch"
    assert "sandbox.commit(" not in src, (
        "the breaker phase runs between commits -- `add -A` here sweeps up a half-finished "
        "round under a message about probes")

    # A commit that does not happen un-keeps the probe rather than reporting it
    # kept: the packet would otherwise name files the branch does not have.
    assert "report.promoted = []" in src, (
        "a probe that could not be committed is not reported kept -- the packet would name "
        "files the branch does not have")

    # And the method it leans on commits those paths and nothing else.
    from factory import sandbox as sandbox_mod
    commit_files = inspect.getsource(sandbox_mod.Sandbox.commit_files)
    assert '"add", "--", *paths' in commit_files
    assert '"commit", "--no-verify", "-m", message, "--", *paths' in commit_files
    assert "user.name=software factory" in commit_files, (
        "the factory's commits stay attributable as one hand")


def test_a_recording_is_swept_after_the_file_that_left_it():
    """A runner that clears its output directory whenever it starts had erased
    every blind file's recording but the last by the time the suite ended --
    which is when they were swept. Playwright does exactly that, so a feature
    whose browser tests were split across two files would have shown a reader
    one file's worth and said nothing about the rest.

    Swept after each file instead, the only moment a recording both exists and
    can be told apart from the next file's.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp, "test-results")
        swept: dict[str, list[str]] = {}

        class _Clearing:
            async def execute(self, command, *, cwd, timeout_s, network=True):
                path = command.split()[-1]
                # What the runner does on start, and then what the test leaves.
                import shutil as _shutil
                _shutil.rmtree(out, ignore_errors=True)
                (out / pathlib.Path(path).stem).mkdir(parents=True)
                (out / pathlib.Path(path).stem / "trace.zip").write_bytes(b"PK")
                return gates.Execution(exit_code=0, output="ok", started=True)

        def sweep(path):
            swept[path] = sorted(p.parent.name for p in out.rglob("trace.zip"))

        rules = [schemas.TestFileCommand(match="*", command="run {path}")]
        asyncio.run(gates.run_per_file(
            rules, ["a.spec.ts", "b.spec.ts"], tmp, _Clearing(), after_file=sweep))

    assert swept == {"a.spec.ts": ["a.spec"], "b.spec.ts": ["b.spec"]}, (
        "each file's recording is taken before the next file's start erases it")

    # And the pipeline wires the sweep in, attributed to the file.
    src = inspect.getsource(pipeline.Factory._assess)
    assert "after_file=sweep" in src
    assert "self._collect_traces(store, state, sandbox, test_path=test_path," in src


def test_a_kept_probe_is_still_run_the_round_after_it_was_kept():
    """Promotion put a probe on the branch and then stopped it being run.

    The guard that refuses to write over a file it did not create met the
    promoted copy at the probe's own path, refused the write, dropped the probe
    out of the run list, and the round reported "the breaker's probes could not
    be run; they prove nothing". A measured round that ran nothing, wearing an
    environment failure's clothes, because a probe had passed. Observed on a
    real revalidate: round 0 found a defect, round 1 fixed it and kept the
    probe, round 2 ran no probes at all.

    Byte-identical content is what tells the two cases apart without asking
    anything. A file this probe would write unchanged is this probe's.
    """
    run = inspect.getsource(pipeline.Factory._run_breaker)
    guard = run[run.index("# Never over a file that belongs"):run.index("started = time.monotonic()")]
    assert 'same = here.read_text(encoding="utf-8") == write.contents' in guard
    assert "(ours if same else taken)" in guard
    # Refused only for a file that is genuinely someone else's, and the message
    # says which case it is.
    assert "a different file is already there" in guard
    # And a probe already in place counts as applied, because what that field
    # means is "this ran".
    assert "set(applied) | set(ours)" in guard
    # The drop from `tests` is only for the refused ones.
    assert "tests = [t for t in tests if t.path not in taken]" in guard


def test_a_probe_that_stops_being_kept_leaves_the_branch():
    """A probe promoted in an earlier round is committed. If a later round
    finds it failing it is deleted from the tree, and the deletion has to reach
    the branch too -- otherwise the packet says the probe is gone while the
    branch still carries it, and the next round meets a file at its path that
    nothing accounts for."""
    run = inspect.getsource(pipeline.Factory._run_breaker)
    assert "dropped = [p for p in earlier if p not in set(report.promoted)]" in run
    assert "report.promoted + dropped" in run, (
        "`git add` stages a removal like an addition, so one commit says both")
    assert "earlier = promoted_this_pass(store.records())" in run
    assert 'payload.get("promoted")' in inspect.getsource(pipeline.promoted_this_pass), \
        "what was kept before comes off the record"


def test_a_gate_report_is_never_committed_to_the_branch(tmp_path):
    """A survey rule `cd`'d and named its report relatively, the report landed
    in `workspace/` where the cleanup did not look, and `git add -A` put 36 of
    them on the card-labels branch."""
    import subprocess
    from factory.sandbox import Sandbox

    repo = _repo_with_one_commit(tmp_path / "kanban", {"api/app.py": "app = 1\n"})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="kanban", feature_id="f1")
    (sandbox.path / "workspace").mkdir()
    (sandbox.path / "workspace/.factory-report-0ccfb34c.xml").write_text("<testsuites/>")
    (sandbox.path / ".factory-report-43461814.xml").write_text("<testsuites/>")
    (sandbox.path / "api/labels.py").write_text("labels = []\n")

    sandbox.commit("factory: Card labels")

    tracked = subprocess.run(["git", "ls-files"], cwd=sandbox.path,
                             capture_output=True, text=True).stdout.split()
    assert "api/labels.py" in tracked
    assert not [p for p in tracked if ".factory-report-" in p]


def test_the_proxy_answers_every_outside_name_with_a_stand_in_and_asks_no_one():
    """The DNS half. An A query from a pool network gets an address in that
    network's stand-in block, the same one for the same name. AAAA is empty, so
    a client goes straight to A rather than to a timeout. Names that are never
    on the internet are NXDOMAIN, a client off the pool is REFUSED, and a
    packet that is not one plain query gets no reply at all."""
    import ipaddress
    import struct
    from factory import egress_proxy as proxy

    table = proxy.StandIns()
    pool = "10.212.0.0/14"

    def ask(name, qtype=1, client="10.212.4.7", packet=None):
        out = proxy.answer(packet or _dns_query(name, qtype), client, table, pool)
        if out is None:
            return None, None, []
        ident, flags, _, count = struct.unpack("!HHHH", out[:8])
        assert ident == 0x1234 and flags & 0x8000 and flags & 0x0100, "id, QR and RD echoed"
        addresses = [".".join(str(b) for b in out[-4:])] if count else []
        return flags & 0xF, count, addresses

    rcode, count, first = ask("Repo.Maven.Apache.org")
    assert (rcode, count) == (0, 1)
    assert ipaddress.ip_address(first[0]) in ipaddress.ip_network("10.212.6.0/23")
    assert ask("repo.maven.apache.org")[2] == first, "one name, one stand-in, any case"
    assert ask("github.com")[2] != first
    assert ipaddress.ip_address(ask("github.com", client="10.212.8.5")[2][0]) in \
        ipaddress.ip_network("10.212.10.0/23"), "each network gets stand-ins on its own subnet"
    assert ask("repo.maven.apache.org", qtype=28)[:2] == (0, 0), "AAAA is empty, not an error"
    for local in ("db", "metadata.google.internal", "4.3.2.1.in-addr.arpa", "printer.local"):
        assert ask(local)[0] == 3, f"{local} was handed a stand-in"
    assert ask("example.com", client="172.17.0.9")[0] == 5, "only the pool is served"
    assert ask("", packet=b"\x00\x01")[0] is None, "garbage gets no reply"


def test_a_level_is_tested_only_with_a_runner_and_a_way_to_clean_up():
    """Tests that can't run cleanly fail for reasons that say nothing about the
    code -- a missing runner, another test's leftovers -- and read as a broken
    feature. So such a level is skipped. Every other answer is tested: a reset,
    tests that keep to their own data, tests that undo their own. A question
    never asked is not a reason to skip, unit tests share no data, and a fix a
    person chose counts at once."""
    from factory.workspace import NO_CLEANUP, NO_RUNNER, unchecked_levels

    assert unchecked_levels(_State(_levels_surface(unit="absent", integration="harness", user="nobody"))) \
        == {"unit": NO_RUNNER, "user": NO_CLEANUP}
    for tested in ("harness", "isolated", "each_test", None):
        assert unchecked_levels(_State(_levels_surface(unit="harness", integration="harness",
                                                user=tested))) == {}, tested
    assert unchecked_levels(_State(_levels_surface(unit="nobody", integration="harness", user="harness"))) == {}, \
        "unit tests share no data; only a missing runner takes them out"
    assert unchecked_levels(_State(_levels_surface(unit="harness", integration="harness", user="nobody"),
                                   applied={"user": "Give each test its own board"})) == {}
    assert unchecked_levels(_State(_levels_surface(unit="harness", integration="harness"))) \
        == {"user": NO_RUNNER}, "a level the reading never found has no runner"


def test_a_test_runners_own_output_is_never_a_units_work(tmp_path):
    """An integrator ran Playwright from `api/`, which writes
    `test-results/.last-run.json` under wherever it started. That was
    collected as the integrator's work and committed, two reviewers flagged a
    stale "failed" on the branch, and no repair could take it off."""
    import subprocess

    from factory.executors import changed_under
    from factory.sandbox import Sandbox

    assert workspace.is_runner_output("api/test-results/.last-run.json")
    assert workspace.is_runner_output("web/playwright-report/index.html")
    assert not workspace.is_runner_output("api/tests/test_results.py")
    assert not workspace.is_runner_output("test-results")        # a file of that name

    repo = _harness_repo(tmp_path)
    (repo / "api" / "test-results").mkdir(parents=True)
    (repo / "api" / "test-results" / ".last-run.json").write_text('{"status": "failed"}')
    (repo / "api" / "real.py").write_text("x = 1\n")
    assert changed_under(repo) == ["api/real.py"]

    src = inspect.getsource(executors.CommandExecutor._classify)
    assert "is_runner_output(path)" in src, "the harness path collects runner output as work"

    # Nor staged for the branch, whoever left it there.
    src = inspect.getsource(Sandbox.commit)
    assert "*NOT_RUNNER_OUTPUT" in src
    out = subprocess.run(["git", "add", "-A", "--", ".", *workspace.NOT_RUNNER_OUTPUT],
                         cwd=repo, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                            capture_output=True, text=True).stdout.split()
    assert staged == ["api/real.py"]


def test_runner_output_stays_off_the_branch_and_its_removal_does_not(tmp_path):
    """Kept off the branch by a pathspec that also hid its deletion: the one
    stray `.last-run.json` already committed could never be removed, and a
    round that left only runner output behind tried to commit nothing."""
    import subprocess
    from factory.sandbox import Sandbox

    repo = _repo_with_one_commit(tmp_path / "kanban", {
        "api/app.py": "app = 1\n", "api/test-results/.last-run.json": '{"status": "failed"}'})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="kanban", feature_id="f1")

    def tracked() -> list[str]:
        return subprocess.run(["git", "ls-files"], cwd=sandbox.path,
                              capture_output=True, text=True).stdout.split()

    (sandbox.path / "web/test-results/run-1").mkdir(parents=True)
    (sandbox.path / "web/test-results/run-1/trace.zip").write_bytes(b"PK")
    assert sandbox.commit("factory: nothing but output") == "", \
        "runner output alone is nothing to commit, not a failed commit"

    (sandbox.path / "api/labels.py").write_text("labels = []\n")
    (sandbox.path / "api/test-results/.last-run.json").unlink()
    assert sandbox.commit("factory: repair")
    assert "api/labels.py" in tracked()
    assert "api/test-results/.last-run.json" not in tracked(), "the removal did not land"
    assert not [p for p in tracked() if "web/test-results" in p]


def test_no_agent_can_change_the_dockerfile(tmp_path):
    """A feature never carries an edit to it onto its branch, and a repair is
    refused it: the environment is as much the measurement as the tests are."""
    import subprocess
    from factory.config import Config
    from factory.sandbox import Sandbox

    repo = _repo_with_one_commit(tmp_path / "demo", {
        "app.py": "app = 1\n", ".fabrika/Dockerfile": "FROM python:3.12\n"})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="demo", feature_id="f1")
    (sandbox.path / ".fabrika/Dockerfile").write_text("FROM anything-that-passes\n")
    (sandbox.path / "app.py").write_text("app = 2\n")
    assert sandbox.commit("factory: work")
    committed = subprocess.run(["git", "show", "HEAD:.fabrika/Dockerfile"], cwd=sandbox.path,
                               capture_output=True, text=True).stdout
    assert committed == "FROM python:3.12\n"
    (sandbox.path / ".fabrika/Dockerfile").write_text("FROM again\n")
    assert sandbox.commit_tracked("factory: fixer") == ""

    config = Config()
    assert pipeline.is_protected(".fabrika/Dockerfile", [], config)
    assert not pipeline.is_protected("app.py", [], config)


def test_history_reads_the_ledger_in_words_and_hides_nothing():
    """Every event names the record it came from. Snapshots are never rows: an
    edit is what changed in it, and a re-run's reason joins the run. Timings
    are left out. A check run says what moved since the one before."""
    from factory import history

    gate = lambda name, cmd="x": {"name": name, "command": cmd}
    records = _ledger(
        {"kind": "project", "meta": {"note": "registered"}, "payload": {"repo": "/r", "gates": []}},
        {"kind": "survey", "meta": {"cost_usd": 0.0, "notional_usd": 1.25},
         "payload": {"gates": [gate("lint")], "environment": {"kind": "compose"}}},
        {"kind": "project", "meta": {"note": "surveyed"}, "payload": {"gates": [gate("lint")]}},
        {"kind": "baseline", "payload": {"results": [
            {"name": "setup[0]", "passed": True}, {"name": "lint", "passed": False}]}},
        {"kind": "check_timing", "payload": {"lint": 1.0}},
        {"kind": "project", "meta": {"note": "edited: gates"},
         "payload": {"gates": [gate("lint", "y"), gate("tests")]}},
        {"kind": "baseline", "payload": {"results": [
            {"name": "lint", "passed": True}, {"name": "tests", "passed": True}]}},
        {"kind": "project", "meta": {"note": "baseline re-run · 2 feature(s) are building against it"},
         "payload": {"gates": [gate("lint", "y"), gate("tests")]}},
        {"kind": "scaffold", "role": "human", "payload": {
            "written": ["a/b.ts"], "commit": "abcdef0123", "commit_problem": ""}},
    )
    out = history.project_history(records, [])
    titles = [e["title"] for e in out["events"]]
    assert all(e["seq"] for e in out["events"]), "an event that cannot be traced to its record"
    assert "You edited the project" in titles
    edit = next(e for e in out["events"] if e["title"] == "You edited the project")
    assert edit["items"] == ["added tests", "changed lint"]
    run = out["events"][1]
    assert run["title"] == "Checks ran: all 2 passed" and run["detail"] == "2 features were building"
    assert run["items"] == ["lint now passes", "tests is new"]
    assert not [e for e in out["events"] if e["seq"] in (3, 5, 8)], "a snapshot or a timing became a row"
    s = out["summary"]
    assert (s["commits"], s["files"], s["check_runs"], s["readings"]) == (1, 1, 2, 1)
    assert s["reading_cost"] == {"billed": 0.0, "notional": 1.25}


def test_committing_fabrikas_own_dockerfile_is_not_tooling_drift(tmp_path):
    """Moving the Dockerfile in, as Fabrika itself proposes, read as a new tool
    the survey had never seen and asked for a re-reading. A change to it is the
    Environment tab's to report; the checks' drift is about the repository's own
    tooling."""
    import subprocess
    from factory.workspace import tooling_drift, tooling_paths

    repo = _repo_with_one_commit(tmp_path / "demo", {"app.py": "x = 1\n", "Dockerfile": "FROM a\n"})
    since = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                           text=True).stdout.strip()
    known = tooling_paths(repo)
    (repo / ".fabrika").mkdir()
    (repo / ".fabrika" / "Dockerfile").write_text("FROM b\n")
    _commit(repo, "move the dockerfile in")
    drift = tooling_drift(repo, since, known)
    assert not drift["stale"], drift
    assert ".fabrika/Dockerfile" not in tooling_paths(repo)

    (repo / "Dockerfile").write_text("FROM c\n")
    _commit(repo, "the project's own image changed")
    assert tooling_drift(repo, since, known)["changed"] == ["Dockerfile"], \
        "the repository's own Dockerfile is still tooling"


def test_v13_the_as_built_never_reaches_the_verify_lane():
    """INV-13. The oracle works from the spec. A description of the code it is
    judging -- however well checked -- is exactly what INV-1 keeps from it, and
    a stale one would manufacture agreement."""
    params = list(inspect.signature(workspace.verify_context).parameters)
    assert params == ["spec", "runtime", "surface", "runtime_contract"] or all(
        "built" not in p and "graph" not in p for p in params)
    lane = inspect.getsource(pipeline.Factory._verify_lane)
    for word in ("asbuilt", "as_built", "AsBuilt", "system_change"):
        assert word not in lane, f"the verify lane mentions {word}"
    assert "asbuilt" not in factory_source("workspace")


def test_nothing_reads_the_as_built_as_part_of_the_repository(tmp_path):
    """A resurvey of clinic was shown twenty as-built pages as the project's
    "test files and setup" -- `conftest.py.md` beside `conftest.py`, sorted
    first, taking the budget ahead of real tests -- and the same listing feeds
    every digest, the scout's slices and the symbol index. What Fabrika writes
    about a repository is not the repository. Its Dockerfile, which is the
    project's own, still is."""
    root = _ab_repo(tmp_path, {
        "tests/conftest.py": "import pytest\n",
        "app/core.py": "def handle_card():\n    return 1\n",
        ".fabrika/Dockerfile": "FROM python:3.12\n",
        ".fabrika/as-built/files/tests/conftest.py.md": "# conftest\n",
        ".fabrika/as-built/files/app/core.py.md": "def handle_card(): described\n",
        ".fabrika/as-built/records/tests/conftest.py.json": "{}\n",
    })
    listed = [p.relative_to(root).as_posix() for p in workspace.iter_repo_files(root)]
    assert ".fabrika/Dockerfile" in listed
    assert not [p for p in listed if p.startswith(".fabrika/as-built/")]
    assert not [p for p in workspace.testing_paths(root) if p.startswith(".fabrika/as-built/")]
    assert ".fabrika/as-built" not in workspace.repo_digest(root)
    index = workspace.symbol_index(root)
    assert all(not f.startswith(".fabrika/as-built") for f in index.by_file)
    assert asbuilt_graph.AS_BUILT_DIR + "/" in workspace.GENERATED_PREFIXES


def test_a_check_whose_script_needs_docker_is_set_aside_with_a_patch_offered(tmp_path):
    (tmp_path / "frontend/e2e/support").mkdir(parents=True)
    (tmp_path / "frontend/e2e/support/start.sh").write_text("docker run -d postgres\n")
    (tmp_path / "frontend/playwright.config.ts").write_text(
        "export default { webServer: [{ command: './e2e/support/start.sh' }] }")
    assert workspace.launched_scripts(tmp_path) == ["frontend/e2e/support/start.sh"]
    assert "frontend/e2e/support/start.sh" in workspace.repo_digest(tmp_path)

    match = onboarding.DOCKER_MISSING.search(
        "[WebServer] ./e2e/support/start.sh: line 13: docker: command not found")
    assert match and match["line"] == "13" and match["script"].endswith("start.sh")
    assert not onboarding.DOCKER_MISSING.search("pytest: 3 failed")


def test_a_proposed_fix_to_the_repository_is_committed_on_a_press(tmp_path):
    import subprocess

    registry, project, repo, fixed = _diagnosed_project(tmp_path)
    out = registry.apply_diagnosis_fix(project, "api-tests", 0)
    assert (repo / "api/pyproject.toml").read_text() == fixed and out["commit"]
    log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo, capture_output=True, text=True)
    assert log.stdout.strip() == "Count greenlets"
    assert any(r.get("kind") == "diagnosis_fix" and r.get("role") == "human"
               for r in registry.get(project.id).store.records())
