"""Writing a file that something else may be reading.

`Path.write_text` empties the file and then fills it. A reader that arrives in
between finds it empty or half-written, and the settings files here are read,
changed and written back: the reader that found nothing writes nothing back,
and a history kept for weeks is gone. Writing beside the file and renaming it
into place means a reader finds the old version or the new one, never neither.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: str | Path, text: str, *, mode: int = 0o644) -> None:
    """Replace `path` with `text` in one step. `mode` holds from the first byte:
    a credential is never readable by others, not even for a moment."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
