"""One bounded environment per feature.

A worktree isolates the code. A container isolates the execution. You need both
before two features can be built on one machine at the same time: a worktree
does nothing about port 5432, a test database, a package cache or a dev server,
and two test suites racing in two checkouts produce results that look like
signal and are not.

This module owns the first half. Gates get the second half through the runner
protocol, which uses the image and container recorded here.

What the human gets at the end is a branch. A git worktree shares the object
database with the repository it came from, so a commit made in here is already
a ref in the project's own repo -- `git diff main...factory/<feature>` works
from the normal checkout, with nothing to export.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .git import has_commit
from .schemas import SandboxState
from .workspace import NOT_A_REPORT, NOT_FABRIKA, NOT_RUNNER_OUTPUT, PathEscape, is_runner_output, SKIP_DIRS, SKIP_SUFFIXES, safe_join

BRANCH_PREFIX = "factory/"
GIT_TIMEOUT = 180


class SandboxError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def resolve_ref(repo: Path, ref: str) -> str:
    """The commit a feature will branch from, pinned at creation.

    Recorded as a sha rather than a ref name so that a feature built today and
    reviewed next week can still be diffed against what it actually started
    from, even if the branch has moved.
    """
    result = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo, timeout=20)
    if result.returncode != 0:
        raise SandboxError(f"cannot resolve {ref!r} in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def _read_lines(path: Path) -> list[str]:
    """A file's lines, or none at all if it is not there or is not text.
    A path that did not exist before is a file with no lines, not an error."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _count_lines(path: Path) -> int:
    return len(_read_lines(path))


def parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    """`git diff --numstat -z --no-renames` into {path: (added, removed)}.

    A binary file records (0, 0) rather than dropping out. It has no lines to
    read, and saying so is different from saying nothing -- a caller that finds
    no entry falls back to counting the whole file, which for a PNG would be a
    number with no meaning behind it.
    """
    out: dict[str, tuple[int, int]] = {}
    for record in output.split("\0"):
        if not record.strip():
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        out[path] = (
            0 if added.strip() == "-" else int(added or 0),
            0 if removed.strip() == "-" else int(removed or 0),
        )
    return out


def working_tree_drift(repo: str | Path, base_ref: str = "HEAD") -> dict:
    """How far the human's working tree has moved from what features branch off.

    A sandbox is `git worktree add <base_ref>`, so uncommitted work is invisible
    to every agent. That failure is silent and expensive: the scout reports
    truthfully on a codebase that is months old, the interrogator asks careful
    questions about inventing something that already exists, and nothing in the
    output looks wrong. Cheap to detect, so detect it.
    """
    repo = Path(repo).expanduser().resolve()
    blank = {
        "checked": False, "files": 0, "modified": 0, "untracked": 0,
        "ahead": 0, "behind": 0, "drifted": False,
        "base_ref": base_ref, "base_sha": "", "head_sha": "", "branch": "", "sample": [],
    }
    if not repo.is_dir() or not has_commit(repo):
        return blank

    status = _git(["status", "--porcelain", "--untracked-files=all"], repo)
    if status.returncode != 0:
        return blank

    modified, untracked, sample = 0, 0, []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        code, _, path = line[:2], line[2:3], line[3:].strip().strip('"')
        # A change to something no agent would ever read is not drift. Flagging
        # a stray .bak under .claude/ trains people to tick the override.
        parts = Path(path).parts
        if any(part in SKIP_DIRS for part in parts) or Path(path).suffix.lower() in SKIP_SUFFIXES:
            continue
        if code == "??":
            untracked += 1
        else:
            modified += 1
        if len(sample) < 12:
            sample.append({"path": path, "state": "untracked" if code == "??" else code.strip()})

    def sha(ref: str) -> str:
        out = _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo, timeout=20).stdout.strip()

    # Uncommitted work is only half of it. Committing onto a feature branch while
    # the project still branches from `main` hides exactly as much, and
    # `git status` says nothing about it -- it reports a clean tree.
    def count(rev_range: str) -> int:
        out = _git(["rev-list", "--count", rev_range], repo, timeout=30)
        try:
            return int(out.stdout.strip()) if out.returncode == 0 else 0
        except ValueError:
            return 0

    ahead = count(f"{base_ref}..HEAD")
    behind = count(f"HEAD..{base_ref}")
    return {
        "checked": True,
        "files": modified + untracked,
        "modified": modified,
        "untracked": untracked,
        "ahead": ahead,
        "behind": behind,
        "drifted": bool(modified + untracked or ahead),
        "base_ref": base_ref,
        "base_sha": sha(base_ref),
        "head_sha": sha("HEAD"),
        "branch": branch,
        "sample": sample,
    }


class Sandbox:
    """A feature's checkout, its branch, and the identity of its container."""

    def __init__(self, state: SandboxState, repo: Path) -> None:
        self.state = state
        self.repo = Path(repo).expanduser().resolve()

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        repo: str | Path,
        root: str | Path,
        project_id: str,
        feature_id: str,
        base_ref: str = "HEAD",
        image: str = "",
    ) -> "Sandbox":
        repo = Path(repo).expanduser().resolve()
        if not repo.exists():
            raise SandboxError(f"project repository does not exist: {repo}")

        path = Path(root).expanduser().resolve() / project_id / feature_id
        if path.exists():
            raise SandboxError(f"a sandbox already exists at {path}; release it before recreating")
        path.parent.mkdir(parents=True, exist_ok=True)

        state = SandboxState(
            feature_id=feature_id, project_id=project_id,
            path=str(path), image=image,
        )

        if has_commit(repo):
            base_sha = resolve_ref(repo, base_ref)
            branch = f"{BRANCH_PREFIX}{feature_id}"
            result = _git(["worktree", "add", "-b", branch, str(path), base_sha], repo)
            if result.returncode != 0:
                raise SandboxError(
                    f"git worktree add failed for {feature_id}: {result.stderr.strip()}"
                )
            state.kind = "worktree"
            state.branch = branch
            state.base_sha = base_sha
        else:
            # No git, or no commits yet. The isolation property still has to
            # hold, so copy -- but say which mode we are in rather than
            # pretending the branch exists.
            shutil.copytree(
                repo, path,
                ignore=shutil.ignore_patterns(*SKIP_DIRS),
                symlinks=True, dirs_exist_ok=True,
            )
            state.kind = "copy"

        return cls(state, repo)

    @classmethod
    def reopen(cls, state: SandboxState, repo: str | Path) -> "Sandbox":
        return cls(state, Path(repo))

    # -- properties -------------------------------------------------------

    @property
    def path(self) -> Path:
        return Path(self.state.path)

    @property
    def branch(self) -> str:
        return self.state.branch

    def exists(self) -> bool:
        return bool(self.state.path) and self.path.is_dir()

    # -- writing ----------------------------------------------------------

    def write(self, relative: str, contents: str) -> Path:
        """Path-checked against escaping the sandbox. Every model-authored path
        goes through here."""
        target = safe_join(self.path, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        return target

    def commit(self, message: str) -> str:
        """Commit everything currently in the sandbox onto its branch.

        Returns the sha, or "" when there was nothing to commit or this sandbox
        is a plain copy. Committing is what turns the run into something the
        human can review with ordinary git.
        """
        if self.state.kind != "worktree" or not self.exists():
            return ""
        # Never a gate's per-test report. One survey rule wrote them where the
        # cleanup did not look, and `add -A` put 36 of them on a feature branch.
        # Nor a runner's own output directory, wherever it was started from.
        # Nor the project's own Fabrika environment: see `NOT_FABRIKA`.
        _git(["add", "-A", "--", ".", NOT_A_REPORT, NOT_FABRIKA, *NOT_RUNNER_OUTPUT], self.path)
        # Except its removal. The exclusion also hides a deletion, so runner
        # output an earlier build already committed could never be taken off
        # the branch -- which is the one thing a repair of it is for.
        missing = _git(["ls-files", "--deleted", "-z"], self.path).stdout.split("\0")
        gone = [p for p in missing if p and is_runner_output(p)]
        if gone:
            _git(["rm", "--cached", "-q", "--", *gone], self.path)
        # What is staged, not what `status` sees: output left beside the code
        # is in the status and deliberately not staged, and committing on the
        # strength of it fails with nothing to commit.
        if _git(["diff", "--cached", "--quiet"], self.path).returncode == 0:
            return ""
        committed = _git(
            ["-c", "user.name=software factory",
             "-c", "user.email=factory@localhost",
             "commit", "--no-verify", "-m", message],
            self.path,
        )
        if committed.returncode != 0:
            raise SandboxError(f"commit failed in {self.path}: {committed.stderr.strip()}")
        sha = _git(["rev-parse", "HEAD"], self.path).stdout.strip()
        self.state.commit_sha = sha
        return sha

    def commit_tracked(self, message: str) -> str:
        """Commit changes to files already tracked, and nothing new.

        For what a fixer did: a formatter rewrites code, and a build or a test
        run beside it leaves output that must not ride along onto the branch.
        Returns the sha, or "" when no tracked file changed.
        """
        if self.state.kind != "worktree" or not self.exists():
            return ""
        _git(["add", "-u", "--", ".", NOT_FABRIKA], self.path)
        if _git(["diff", "--cached", "--quiet"], self.path).returncode == 0:
            return ""
        committed = _git(
            ["-c", "user.name=software factory",
             "-c", "user.email=factory@localhost",
             "commit", "--no-verify", "-m", message],
            self.path,
        )
        if committed.returncode != 0:
            raise SandboxError(f"commit failed in {self.path}: {committed.stderr.strip()}")
        sha = _git(["rev-parse", "HEAD"], self.path).stdout.strip()
        self.state.commit_sha = sha
        return sha

    def commit_file(self, relative: str, message: str) -> tuple[str, str]:
        """Commit exactly one path, and nothing else that happens to be staged.

        Returns (sha, problem). A problem is a sentence for a human, never an
        exception: this runs after the work is finished and already committed,
        so a failure here means "the branch is good and one document did not
        land on it", which must not be allowed to lose the run.

        Separate from `commit` because that one is `add -A`, which is right for
        collecting a worker's output and wrong for appending a document to a
        tree somebody is about to read. Same authorship, so the factory's
        commits are attributable as one hand.
        """
        if self.state.kind != "worktree" or not self.exists():
            return "", "this sandbox is not a worktree, so nothing was committed."
        try:
            safe_join(self.path, relative)
        except PathEscape:
            return "", f"{relative!r} is outside the sandbox."
        if _git(["add", "--", relative], self.path).returncode != 0:
            return "", f"`git add` failed for {relative}."
        staged = _git(["status", "--porcelain", "--", relative], self.path)
        if not staged.stdout.strip():
            return "", ""            # already identical on the branch
        committed = _git(
            ["-c", "user.name=software factory",
             "-c", "user.email=factory@localhost",
             "commit", "--no-verify", "-m", message, "--", relative],
            self.path,
        )
        if committed.returncode != 0:
            return "", (
                f"`git commit` failed for {relative}: "
                f"{committed.stderr.strip()[:200]}. It is written and staged."
            )
        return _git(["rev-parse", "HEAD"], self.path).stdout.strip(), ""

    def commit_files(self, relatives: Sequence[str], message: str) -> tuple[str, str]:
        """Commit exactly these paths, and nothing else that happens to be staged.

        `commit_file` for more than one. Separate from `commit` for the reason
        given there -- that one is `add -A`, which is right for collecting a
        worker's output and wrong for adding a named set of files to a tree
        somebody else is mid-way through building.

        The caller is a phase that runs between commits rather than after the
        last one, so a pathspec is not decoration: sweeping up whatever else is
        in the tree would put a half-finished round on the branch under this
        message.
        """
        if self.state.kind != "worktree" or not self.exists():
            return "", "this sandbox is not a worktree, so nothing was committed."
        paths = []
        for relative in relatives:
            try:
                safe_join(self.path, relative)
            except PathEscape:
                return "", f"{relative!r} is outside the sandbox."
            paths.append(relative)
        if not paths:
            return "", ""
        if _git(["add", "--", *paths], self.path).returncode != 0:
            return "", f"`git add` failed for {', '.join(paths)}."
        staged = _git(["status", "--porcelain", "--", *paths], self.path)
        if not staged.stdout.strip():
            return "", ""            # already identical on the branch
        committed = _git(
            ["-c", "user.name=software factory",
             "-c", "user.email=factory@localhost",
             "commit", "--no-verify", "-m", message, "--", *paths],
            self.path,
        )
        if committed.returncode != 0:
            return "", (
                f"`git commit` failed for {', '.join(paths)}: "
                f"{committed.stderr.strip()[:200]}. They are written and staged."
            )
        return _git(["rev-parse", "HEAD"], self.path).stdout.strip(), ""

    def head(self) -> str:
        """The sha the branch is on right now, or "" if this is not a worktree."""
        if self.state.kind != "worktree" or not self.exists():
            return ""
        return _git(["rev-parse", "HEAD"], self.path).stdout.strip()

    def reset_to(self, sha: str) -> str:
        """Undo everything after `sha`, working tree included.

        Used by exactly one caller: a repair round whose gates came back worse
        than the round before it. Nothing is lost by doing this -- the round's
        outputs, its prompts and its diff are already in the evidence store,
        which is append-only -- but the branch a human reads should not carry a
        change that made the work worse.
        """
        if self.state.kind != "worktree" or not self.exists() or not sha:
            return ""
        result = _git(["reset", "--hard", sha], self.path)
        if result.returncode != 0:
            raise SandboxError(f"could not reset {self.path} to {sha}: {result.stderr.strip()}")
        _git(["clean", "-fd"], self.path)
        self.state.commit_sha = sha
        # The clean takes untracked files with it, and some of those are what
        # the environment's setup put here. Forgetting that setup ran is the
        # honest state: the next assessment reinstalls rather than running the
        # gates against a checkout that is missing its toolchain.
        self.state.setup_at = ""
        return sha

    def diff_command(self) -> str:
        """What the human types to see what was built."""
        if self.state.kind != "worktree" or not self.state.branch:
            return f"diff -ru {self.repo} {self.state.path}"
        base = self.state.base_sha[:12] or "HEAD"
        return f"git diff {base}...{self.state.branch}"

    def changed_files(self) -> list[str]:
        if self.state.kind != "worktree" or not self.exists():
            return []
        if self.state.commit_sha and self.state.base_sha:
            result = _git(
                ["diff", "--name-only", self.state.base_sha, self.state.commit_sha], self.path
            )
        else:
            result = _git(["status", "--porcelain", "--untracked-files=all"], self.path)
            return [line[3:].strip().strip('"') for line in result.stdout.splitlines() if line.strip()]
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def show(self, ref: str, relative: str) -> str | None:
        """A file's contents at a commit, or None if it was not there.

        The only honest way to ask what a change removed. Reading it from an
        agent's account of its own diff asks the thing being audited to describe
        itself; `git show` is the same question put to the repository.
        """
        if self.state.kind != "worktree" or not self.exists() or not ref:
            return None
        result = _git(["show", f"{ref}:{relative}"], self.path)
        if result.returncode != 0:
            return None
        return result.stdout

    def file_diff(self, relative: str, *, context: int = 3) -> tuple[str, str]:
        """What this feature did to one file, as a patch. Returns (diff, problem).

        The console has never been able to show a diff, because an agent
        returns whole files -- `FileWrite.contents` is the complete file, never
        a fragment -- so the only before-and-after anywhere is the one the
        repository holds. This is `show` asked about both sides at once, and
        for the same reason: the question goes to git, not to the thing being
        audited.

        A problem is a sentence for a human rather than an exception. Every
        caller here is a screen someone is reading, and a review that 500s
        because one file moved is worse than one that says so in the row.
        """
        if self.state.kind != "worktree" or not self.exists():
            return "", "this feature has no checkout any more, so there is nothing to diff."
        try:
            safe_join(self.path, relative)
        except PathEscape:
            return "", f"{relative!r} is outside the sandbox."
        base = self.state.base_sha
        if not base:
            return "", "nothing recorded what this branch was cut from."
        # The tip, not the build's commit: the repair loop moves it every round,
        # and a diff against the first commit would hide everything the loop did.
        head = self.head() or self.state.commit_sha
        if not head:
            return "", "this branch has no commit on it yet."
        result = _git(
            ["diff", f"--unified={context}", "--no-color", "--find-renames",
             base, head, "--", relative],
            self.path,
        )
        if result.returncode != 0:
            return "", f"`git diff` failed for {relative}: {result.stderr.strip()[:200]}"
        # Empty is a fact, not a failure: a file the packet lists and the branch
        # did not change is exactly what the reader needs told.
        return result.stdout, ""

    def diffstat(self, paths: Sequence[str] = ()) -> dict[str, tuple[int, int]]:
        """Lines added and removed per path, against what this feature branched
        from. The same question as `show`, put to the repository.

        Every attention figure in the packet is a sum over these. They have to
        mean what a reviewer means by them, which counting what an agent
        returns does not: an agent returns whole files -- `FileWrite.contents`
        is the complete file, never a fragment -- so that count measures the
        size of the file that was touched, not the size of the change that was
        made. A repair unit that adds six lines of comment to an 800-line seed
        module would be reported as 800 lines of work nobody asked for, on the
        one screen whose entire job is telling a human how much reading is in
        front of them. The agents would not be exaggerating; the arithmetic
        would.

        Measured against the working tree rather than `HEAD` so that writes not
        yet committed still count, and a file git has never seen counts as
        wholly added. `paths` narrows the answer, and is required in `copy`
        mode, where there is no index to enumerate a change from.
        """
        if not self.exists():
            return {}
        if self.state.kind != "worktree" or not self.state.base_sha:
            return self._diffstat_by_reading(paths)

        out: dict[str, tuple[int, int]] = {}
        result = _git(
            ["diff", "--numstat", "-z", "--no-renames", self.state.base_sha], self.path
        )
        if result.returncode != 0:
            return self._diffstat_by_reading(paths)
        out.update(parse_numstat(result.stdout))
        # Tracked files are the diff; a file git has not been told about yet is
        # still a file this run wrote, and counting it as nothing would hide the
        # newest work of all. Scoped to what was asked about when anything was:
        # environment setup runs in here, and an unscoped sweep would open every
        # file `npm install` left behind to count its lines.
        untracked = _git(
            ["ls-files", "--others", "--exclude-standard", "-z", "--", *paths], self.path
        )
        for rel in untracked.stdout.split("\0"):
            rel = rel.strip()
            if not rel or rel in out:
                continue
            out[rel] = (_count_lines(self.path / rel), 0)
        return out

    def _diffstat_by_reading(self, paths: Sequence[str]) -> dict[str, tuple[int, int]]:
        """The same measurement without git: read both versions and compare.

        A `copy` sandbox has no base commit to diff against, so the file as it
        stands in the project's own repository is the before. Slower and limited
        to the paths asked about, but it is the real change either way -- the
        alternative is reporting a file's size as though it were a diff, which
        is what this exists to prevent.
        """
        out: dict[str, tuple[int, int]] = {}
        for rel in paths:
            try:
                after = _read_lines(safe_join(self.path, rel))
                before = _read_lines(safe_join(self.repo, rel))
            except PathEscape:
                continue
            added = removed = 0
            for line in difflib.unified_diff(before, after, n=0, lineterm=""):
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
            out[rel] = (added, removed)
        return out

    # -- teardown ---------------------------------------------------------

    def release(self, *, keep_branch: bool = True) -> None:
        """Remove the checkout. The branch survives by default -- it is the
        deliverable, and it costs nothing to keep."""
        if not self.state.path:
            return
        if self.state.kind == "worktree" and self.path.exists():
            _git(["worktree", "remove", "--force", str(self.path)], self.repo)
            _git(["worktree", "prune"], self.repo)
            if not keep_branch and self.state.branch:
                _git(["branch", "-D", self.state.branch], self.repo)
        shutil.rmtree(self.path, ignore_errors=True)
        from datetime import datetime, timezone
        self.state.released_at = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def prune(repo: str | Path) -> None:
        """Drop worktree registrations whose directories are gone.

        Sandboxes are real directories and real containers on somebody's laptop,
        so they leak. This runs at boot.
        """
        repo = Path(repo).expanduser().resolve()
        if repo.exists() and has_commit(repo):
            _git(["worktree", "prune"], repo)
