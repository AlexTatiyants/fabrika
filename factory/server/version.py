"""Whether the code answering is the code on disk."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .context import Context


#: When this process imported its code, and what that code WAS.
#:
#: The timestamp alone is a proxy for "changed" and it lies in the direction
#: that matters: anything which rewrites a file with identical bytes -- git
#: checkout, a restore from a backup, a formatter that changes nothing -- moves
#: the mtime and means nothing. A check on mtime alone raises a false alarm
#: inside the hour, which is how an indicator stops being read. So mtime is
#: only a cheap filter, and content decides.
_STARTED_AT = time.time()


def _module_digest() -> tuple[float, str, str]:
    """The newest mtime, the file it belongs to, and a hash of every module.

    The hash is the answer; the mtime is a hint for the sentence a human reads,
    because "gates.py was edited" is more use than "something changed".
    """
    root = Path(__file__).resolve().parent.parent     # factory/, not just this package
    newest, where = 0.0, ""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            body = path.read_bytes()
            stamp = path.stat().st_mtime
        except OSError:
            continue
        digest.update(path.name.encode())
        digest.update(body)
        if stamp > newest:
            newest, where = stamp, path.relative_to(root.parent).as_posix()
    return newest, where, digest.hexdigest()


#: What this process is actually running, taken once at import.
_LOADED_DIGEST = _module_digest()[2]


def register(app: FastAPI, ctx: Context) -> None:
    """The running build, against the code on disk."""

    @app.get("/api/version")
    def running_version() -> dict[str, Any]:
        """Whether the code answering you is the code on disk.

        Nothing on any screen said which build produced what you were reading,
        and that cost real money three times in one session. A console started
        before a field existed round-tripped projects through its own older
        shape and deleted `services`, `test_prepare` and `test_file_commands`
        from the stored record. A reloader wedged and left a process holding the
        port and answering nothing. And a fix to what counts as test setup sat
        on disk for two hours while a human paid for a re-survey that could not
        possibly have come out differently -- the answer was byte-identical to
        the one before it, and nothing anywhere said why.

        Only Python is counted. Role prompts are read at call time (INV-10) and
        the console's own files are served per request, so editing either is
        live and reporting it as stale would be crying wolf -- which is how an
        indicator like this stops being read.
        """
        newest, where, digest = _module_digest()
        # Content, not timestamps. A file rewritten with the same bytes is not a
        # change, and saying it is teaches a human to ignore the one time it is.
        stale = digest != _LOADED_DIGEST
        return {
            "started_at": _STARTED_AT,
            "newest_source_at": newest,
            "newest_source": where if stale else "",
            "stale": stale,
            "stale_by_s": max(0.0, round(newest - _STARTED_AT, 1)) if stale else 0.0,
        }
