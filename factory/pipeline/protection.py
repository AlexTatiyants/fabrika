"""The verification surface: the paths a repair must not touch (INV-12).
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Collection, Sequence

from ..config import Config
from ..workspace import FABRIKA_DIR

from .checks.seams import is_seam_check


def protected_paths(
    config: Config, root: Path, extra: Sequence[str] = (),
    own_dirs: Sequence[str] = (), project_files: Collection[str] = (),
) -> list[str]:
    """The verification surface: everything a repair must not touch. (INV-12)

    The oracle's blind tests, the breaker's tests, and the configuration that
    decides which tests run at all. A repairer able to edit these could make any
    finding go away without fixing anything, which turns the loop into a machine
    for manufacturing green -- the exact opposite of what it is for.

    Globs are matched against the repo-relative path, and the set is computed
    from what is actually on disk plus what this run wrote, never from a model's
    account of what it wrote.
    """
    out: set[str] = {e for e in extra if e}
    globs = list(config.rework.protected_globs)
    # `own_dirs` is where this project actually puts its blind tests, which is a
    # property of the repository and not of this tool's configuration. Missing
    # it here would leave the new directories unprotected while looking
    # protected, and INV-12 with a hole in it is invisible: the suite still
    # passes after a repairer edits the test that was failing.
    #
    # Except the project's own files in there. Where a runner keeps its tests
    # beside the code -- Angular's specs in `src/app` -- the blind directory IS
    # the source directory, and protecting it whole would protect the feature:
    # a repair to `app.html` is refused as "a test that judges it", and the
    # defect it fixes ships to the packet unrepaired. See `is_protected`.
    own_globs: list[str] = []
    for own_dir in (config.rework.breaker_dir, config.rework.oracle_dir, *own_dirs):
        own_dir = (own_dir or "").strip("/")
        if own_dir:
            own_globs.append(f"{own_dir}/**")
            own_globs.append(f"{own_dir}/*")
    mine = set(project_files)
    # The integrator's seam checks have no directory of their own -- they sit
    # in the project's test tree beside its own tests -- so they are protected
    # by the marker in their name. `is_protected` matches it without regard to
    # case; this listing walks the disk with globs, which do not, and the
    # marker the integrator is told to use is lower case.
    marker = (config.rework.seam_marker or "").strip().lower()
    if marker:
        globs.append(f"**/*{marker}*")
    if root.exists():
        for pattern in [*globs, *own_globs]:
            owned = pattern in own_globs
            for match in root.glob(pattern):
                if match.is_file():
                    try:
                        rel = match.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    if not (owned and rel in mine):
                        out.add(rel)
    return sorted(out | {e for e in extra if e})


def project_files_in(
    root: Path, base_sha: str, dirs: Sequence[str], written: Sequence[str] = (),
) -> set[str]:
    """The project's own files inside `dirs`: tracked at `base_sha`, or written by the build.

    Read from git rather than from anyone's account. A directory that holds
    only blind tests has none, and stays protected whole.
    """
    wanted = [d.strip("/") for d in dirs if d and d.strip("/")]
    if not wanted:
        return set()
    out: set[str] = set()
    if base_sha:
        try:
            listed = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", base_sha, "--", *wanted],
                cwd=root, capture_output=True, text=True, timeout=60)
            if listed.returncode == 0:
                out.update(line.strip() for line in listed.stdout.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    out.update(p for p in written
               if any(p == d or p.startswith(d + "/") for d in wanted))
    return out


def is_protected(
    path: str, protected: Sequence[str], config: Config,
    own_dirs: Sequence[str] = (), project_files: Collection[str] = (),
) -> bool:
    """Whether one path is on the verification surface.

    Checked by pattern as well as by name, because a repair can create a
    `conftest.py` that did not exist when the set was computed -- and a new one
    is the more interesting case, not the less.

    `project_files` are the project's own files in a blind directory -- tracked
    at the base commit, or written by the build -- and the directory rule does
    not reach them. A blind test, a probe, or a file a repair tries to add
    there is still refused; the source beside them is not.
    """
    if path in set(protected):
        return True
    # The environment the checks run in is as much the measurement as the
    # tests are: a repair that edited it could make a failure go away by
    # changing where it is measured.
    if path == FABRIKA_DIR or path.startswith(FABRIKA_DIR + "/"):
        return True
    pure = PurePosixPath(path)
    if is_seam_check(path, config.rework.seam_marker):
        return True
    for own_dir in (config.rework.breaker_dir, config.rework.oracle_dir, *own_dirs):
        own_dir = (own_dir or "").strip("/")
        if (own_dir and (path == own_dir or path.startswith(own_dir + "/"))
                and path not in project_files):
            return True
    for pattern in config.rework.protected_globs:
        if pure.match(pattern):
            return True
    return False
