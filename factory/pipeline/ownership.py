"""Which process owns a feature, and whether it is still alive.
"""

from __future__ import annotations


from ..schemas import FeatureState, GateReport


def this_process() -> str:
    """An identity for the process, stable while it lives and not reused after.

    The pid alone is not enough: they are recycled, and a recycled one reads as
    a run that is still going. The creation time disambiguates, and where it
    cannot be read the pid alone is still a better answer than the feature's
    stage field.
    """
    import os

    started = ""
    try:  # Linux: field 22 of /proc/<pid>/stat is the process start time.
        with open(f"/proc/{os.getpid()}/stat", encoding="utf-8") as fh:
            started = fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        try:  # BSD and macOS.
            import subprocess

            started = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(os.getpid())],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            started = ""
    return f"{os.getpid()}:{started}"


def _blocked_reason(gates: "GateReport") -> str:
    """What the runner said stopped it, quoted rather than re-described.

    Every blocked gate carries the same `[blocked] ...` line, because one
    failed preparation blocks them all -- so the first one is the reason.
    """
    for result in gates.results:
        for line in (result.output_tail or "").splitlines():
            if line.startswith("[blocked]"):
                return line[len("[blocked]"):].strip().rstrip(".") + "."
    return "The runner reported no gate as started."


class HarnessBlocked(RuntimeError):
    """The checks never ran, so there is nothing to say about the code.

    Raised rather than returned because every station after it -- the arbiter,
    the remediator, the repairers -- exists to act on a measurement, and there
    is none. Stopping the line is the honest outcome: the branch is untouched,
    the evidence says why, and `retry-build` picks it back up once the machine
    is fixed.
    """


def owner_is_alive(owner: str) -> bool:
    """Whether the process that claimed a feature is still there.

    Two ways to be sure it is not: the pid is gone, or it is present and
    belongs to something else now. The second matters more than it sounds --
    a recycled pid on a busy machine is the case where a wrong answer here
    locks a feature permanently.
    """
    import os

    if not owner:
        return False
    pid, _, started = owner.partition(":")
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        # Alive and owned by somebody else. Not ours, and not gone.
        return True
    if not started:
        return True
    return this_process().partition(":")[2] == started or _pid_started(pid) == started


def _pid_started(pid: str) -> str:
    import subprocess

    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        pass
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def is_orphaned(state: FeatureState) -> bool:
    """A feature that says it is working, with nobody working on it.

    The state a crash leaves behind. Distinguished from a live run by asking
    the operating system rather than by reading the field that the crash is
    the reason nobody updated.
    """
    return state.stage in ("intake", "building") and not owner_is_alive(state.owner)
