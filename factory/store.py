"""Append-only evidence.

Every agent exchange lands in one JSONL file per feature. There is no update
path and no delete path -- not as a policy, as an absence. A late agent must not
be able to launder an early agent's disclosed gap. (INV-2)

State is not mutated either: a state change is a new `state` record, and
`latest` wins.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from pydantic import BaseModel


def jsonable(value: Any) -> Any:
    """Pydantic models, dicts, lists and paths, all the way down."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass
class _Index:
    """Where each kind of record sits in one ledger file, and how far it is read.

    A ledger runs to megabytes, and a store is asked for one kind at a time --
    the latest `state`, whether there is a `packet` -- dozens of times while
    one page is drawn, and decoding the whole file for each is the cost. The
    file only ever grows, so what has been read once stays true: this keeps the
    byte offset of every record by kind, and reads only what was appended
    since. It holds positions, never records, so every answer is decoded fresh
    and no caller can change what the next one is handed.
    """
    dev: int = -1
    ino: int = -1
    offset: int = 0          # bytes consumed: always just past a newline
    lines: int = 0           # non-blank lines consumed, decodable or not
    tail: bool = False       # a non-blank line is still being written
    kinds: dict[str, list[tuple[int, int]]] = field(default_factory=dict)  # kind -> [(offset, seq)]


_INDEXES: dict[Path, _Index] = {}
_INDEX_LOCK = threading.Lock()


def _index(file: Path) -> _Index:
    """The index of `file`, brought up to its current end."""
    with _INDEX_LOCK:
        try:
            st = os.stat(file)
        except FileNotFoundError:
            _INDEXES.pop(file, None)
            return _Index()
        idx = _INDEXES.get(file)
        # Replaced, not appended to: a discarded feature started again under
        # the same id. Nothing read from the old file says anything about it.
        if idx is None or (idx.dev, idx.ino) != (st.st_dev, st.st_ino) or st.st_size < idx.offset:
            idx = _INDEXES[file] = _Index(dev=st.st_dev, ino=st.st_ino)
        if st.st_size == idx.offset:
            return idx
        with file.open("rb") as fh:
            fh.seek(idx.offset)
            chunk = fh.read(st.st_size - idx.offset)
        # Only whole lines. A record still being written is read once it ends.
        end = chunk.rfind(b"\n") + 1
        idx.tail = bool(chunk[end:].strip())
        at = idx.offset
        for raw in chunk[:end].splitlines(keepends=True):
            if raw.strip():
                idx.lines += 1
                try:
                    r = json.loads(raw)
                    idx.kinds.setdefault(r.get("kind"), []).append((at, r.get("seq", 0)))
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    pass
            at += len(raw)
        idx.offset += end
        return idx


class EvidenceStore:
    """AC-4.1 -- `<root>/<feature_id>/evidence.jsonl`, append and read only."""

    def __init__(self, root: str | Path, feature_id: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.feature_id = feature_id
        self.dir = self.root / feature_id
        self.file = self.dir / "evidence.jsonl"
        self._lock = threading.Lock()
        # Nothing on disk until something is written. A store is constructed to
        # *look*, so one that created its directory and an empty ledger on
        # construction would leave one behind for every request for a feature
        # that does not exist: a feature discarded a minute earlier would come
        # back as an empty directory the moment the console polled it, and a
        # URL carrying a literal "null" would make a project of the same name.
        # Reading is not an event; only a record is.
        idx = _index(self.file)
        self._seq = idx.lines + idx.tail
        # Set by the orchestrator to `LLM.take_usage`. The store is the only
        # place that sees both the role and the prompt at write time, which is
        # what identifies the exchange whose cost is waiting to be written.
        self.usage_of: Callable[[str, str], dict[str, Any] | None] | None = None

    # -- write ------------------------------------------------------------

    def append(
        self,
        kind: str,
        payload: Any,
        *,
        role: str = "",
        model: str = "",
        spec_hash: str = "",
        meta: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """AC-4.2 -- one record, monotonic `seq`, wall-clock timestamp.

        INV-2 says every agent *exchange* is recorded. An exchange has two
        halves, and keeping only one -- what an agent said and never what it was
        shown -- makes "why did it conclude that" unanswerable. Prompts go to an
        artifact file rather than inline,
        because a scout prompt is 120,000 characters and would make the ledger
        unreadable.
        """
        with self._lock:
            # The one place a store is allowed to touch the disk.
            self.dir.mkdir(parents=True, exist_ok=True)
            self._seq += 1
            meta = dict(meta or {})
            if prompt is not None:
                target = self.artifact_dir("prompts") / f"{self._seq:04d}-{role or kind}.md"
                target.write_text(prompt, encoding="utf-8")
                meta["prompt_file"] = str(target.relative_to(self.dir))
                meta["prompt_chars"] = len(prompt)
                # What the exchange cost, if this half was bought from a model
                # by this process. A harness prompt handed to another tool, or a
                # run from before the accounting existed, records nothing --
                # unknown and zero are different claims and the console reads
                # them differently.
                spent = self.usage_of(role, prompt) if self.usage_of else None
                if spent:
                    meta.update(spent)
            record = {
                "seq": self._seq,
                "at": datetime.now(timezone.utc).isoformat(),
                "feature_id": self.feature_id,
                "kind": kind,
                "role": role,
                "model": model,
                "spec_hash": spec_hash,
                "meta": jsonable(meta or {}),
                "payload": jsonable(payload),
            }
            with self.file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            return record

    # -- read -------------------------------------------------------------

    def __iter__(self) -> Iterator[dict[str, Any]]:
        """AC-4.3 -- iteration in write order."""
        if not self.file.exists():
            return
        with self.file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def records(self) -> list[dict[str, Any]]:
        return list(self)

    def _read(self, offsets: Sequence[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not offsets:
            return out
        with self.file.open("rb") as fh:
            for at in offsets:
                fh.seek(at)
                out.append(json.loads(fh.readline()))
        return out

    def all(self, kind: str) -> list[dict[str, Any]]:
        return self._read([at for at, _ in _index(self.file).kinds.get(kind, ())])

    def of_kinds(self, *kinds: str) -> list[dict[str, Any]]:
        """Every record of any of `kinds`, in write order -- for folds over a few kinds."""
        index = _index(self.file).kinds
        return self._read(sorted(at for k in kinds for at, _ in index.get(k, ())))

    def latest(self, kind: str) -> dict[str, Any] | None:
        found = _index(self.file).kinds.get(kind)
        return self._read([found[-1][0]])[0] if found else None

    def current(self, kind: str, invalidated_by: Sequence[str] = ("correction",)) -> dict[str, Any] | None:
        """`pipeline.current_record` without decoding the ledger to answer it."""
        kinds = _index(self.file).kinds
        cutoff = max((seq for k in invalidated_by for _, seq in kinds.get(k, ())), default=-1)
        found = next((at for at, seq in reversed(kinds.get(kind, ())) if seq > cutoff), None)
        return self._read([found])[0] if found is not None else None

    def payload(self, kind: str) -> Any:
        r = self.latest(kind)
        return r["payload"] if r else None

    def payloads(self, kind: str) -> list[Any]:
        return [r["payload"] for r in self.all(kind)]

    def has(self, kind: str) -> bool:
        return bool(_index(self.file).kinds.get(kind))

    # -- binary evidence --------------------------------------------------

    def artifact_dir(self, name: str) -> Path:
        """AC-4.4 -- somewhere for QA recordings, traces, coverage output."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        d = self.dir / "artifacts" / safe
        d.mkdir(parents=True, exist_ok=True)
        return d

    # AC-4.5 -- there is intentionally no update, delete, remove or set. (INV-2)


def list_feature_ids(root: str | Path) -> list[str]:
    p = Path(root).expanduser().resolve()
    if not p.exists():
        return []
    out = []
    for child in sorted(p.iterdir()):
        if child.is_dir() and (child / "evidence.jsonl").exists():
            out.append(child.name)
    return out
