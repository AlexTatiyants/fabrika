"""Reading a repository into its as-built.

Deterministic control flow around two roles (INV-6). The reader is asked what
one file contains, once per file and in parallel; code joins and checks the
answers (`asbuilt_graph`); the cartographer names the groups code formed and
groups the ways in into capabilities; `asbuilt_pages` writes the result.

A reading is always of one commit, read from git's objects rather than the
working copy, so what it describes is exactly what that commit holds. A file
whose content has not changed since it was last read is not read again: its
record is kept by content, so a refresh costs about what the change touched.

Where the result goes is the caller's choice. A person's refresh writes it into
the repository under `.fabrika/as-built/` and commits only those paths -- the
rule `.fabrika/Dockerfile` already follows. A feature's readings, at its base
and at its head, stay in that feature's ledger; nothing under `.fabrika/` is
ever staged on a feature branch.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .asbuilt_graph import (
    AS_BUILT_DIR,
    AS_BUILT_VERSION,
    AsBuilt,
    Source,
    apply_account,
    apply_capabilities,
    apply_architecture,
    apply_names,
    architecture_prompt,
    assign_codes,
    build,
    carry_architecture,
    carry_capabilities,
    source_notes,
)
from .llm import RateLimited, RouteExhausted
from .schemas import ArchitectureAccount, CapabilityMap, FileRecord, FileRecordBatch, GroupNaming, SystemAccount
from . import git
from .git import head_sha, is_repo
from .workspace import SKIP_DIRS, SKIP_SUFFIXES, _kept, commit_paths

#: A file this long is read in chunks along its own lines; each chunk is one call.
CHUNK_LINES = 1200
CHUNK_CHARS = 60_000
#: Past this a file is not read at all, and the coverage record says so. A
#: repository's own code is almost never this large; a generated file often is.
MAX_READ_BYTES = 2_000_000
#: Lines this long on average mean minified or generated output.
MINIFIED_LINE = 400
#: The whole file list goes to the reader up to this many paths, so it can
#: resolve an import to a path. Past it, the reader sees its own directory's
#: neighbours and the top-level directories, and code resolves the rest.
TREE_PATHS = 1500
#: Small files are read several to a call. A call carries a fixed cost -- the
#: harness's own system prompt, the reader's instructions, the schema, about
#: 11,000 tokens on Claude Code -- and the median file is under a thousand, so
#: one file a call spends nine tenths of every call on the same preamble --
#: 3,277 calls for 3,061 files, on one large repository.
BATCH_CHARS = 48_000
#: No more files than this in one call: each answer is its own record, and a
#: long answer is where a model starts to merge and drop.
BATCH_FILES = 10
#: A file larger than this is read on its own.
BATCH_FILE_CHARS = 16_000
#: A batched answer listing fewer functions than this share of what the file
#: plainly defines -- once it plainly defines at least this many -- is thin,
#: and the file is read again alone. Read in batches, one project's
#: `lib/api.ts` came back with 1 of its 32 functions, its schemas with 0 of 15.
BATCH_RECHECK_SHARE = 0.5
BATCH_RECHECK_MIN = 4
#: Groups named per cartographer call. More than this and the answers thin out.
NAMES_PER_CALL = 12

ProgressCallback = Callable[[str, str, str], None]


class AsBuiltError(RuntimeError):
    """A reading could not be made, in a sentence for a person."""


class _Stopped(Exception):
    """Internal: the reading stopped asking, at a plan's limit."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# what is at a commit
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str, input_bytes: bytes | None = None, timeout: int = 120) -> bytes:
    r = git.run(args, repo, input=input_bytes, text=False, timeout=timeout)
    if r is None:
        raise AsBuiltError(f"`git {' '.join(args[:2])}` could not run, or ran out of time")
    if r.returncode != 0:
        raise AsBuiltError(f"`git {' '.join(args[:2])}` failed: {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def _skipped_why(path: str) -> str:
    parts = path.split("/")
    if path.startswith(AS_BUILT_DIR + "/"):
        return "the as-built itself"
    if any(p in SKIP_DIRS for p in parts[:-1]) and not _kept(Path(path)):
        return "dependency, build output or tool state"
    low = path.lower()
    if any(low.endswith(s) for s in SKIP_SUFFIXES):
        return "binary or generated"
    return ""


def sources_at(repo: str | Path, commit: str) -> tuple[dict[str, Source], list[dict[str, str]]]:
    """Every text file at `commit`, and what was left out and why."""
    root = Path(repo).expanduser().resolve()
    listing = _git(root, "ls-tree", "-r", "-z", "-l", commit)
    entries: list[tuple[str, str, int]] = []
    skipped: list[dict[str, str]] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        meta, _, name = raw.partition(b"\t")
        bits = meta.split()
        if len(bits) < 4 or bits[1] != b"blob":
            continue
        path = name.decode("utf-8", errors="replace")
        why = _skipped_why(path)
        if why:
            if why != "the as-built itself":
                skipped.append({"path": path, "why": why})
            continue
        size = int(bits[3]) if bits[3].isdigit() else 0
        if size > MAX_READ_BYTES:
            skipped.append({"path": path, "why": f"too large to read ({size // 1000:,} KB)"})
            continue
        entries.append((path, bits[2].decode(), size))
    out: dict[str, Source] = {}
    if entries:
        batch = _git(root, "cat-file", "--batch", input_bytes="".join(f"{b}\n" for _, b, _ in entries).encode(),
                     timeout=600)
        pos = 0
        for path, blob, _ in entries:
            header_end = batch.index(b"\n", pos)
            header = batch[pos:header_end].split()
            size = int(header[2]) if len(header) >= 3 and header[2].isdigit() else 0
            body = batch[header_end + 1: header_end + 1 + size]
            pos = header_end + 1 + size + 1
            if b"\0" in body[:4096]:
                skipped.append({"path": path, "why": "binary"})
                continue
            text = body.decode("utf-8", errors="replace")
            rows = text.count("\n") + 1
            if rows and len(text) / rows > MINIFIED_LINE:
                skipped.append({"path": path, "why": "minified or generated"})
                continue
            out[path] = Source(path=path, blob=blob, text=text)
    return out, sorted(skipped, key=lambda s: s["path"])


def changed_since(repo: str | Path, commit: str, ref: str = "HEAD") -> list[str] | None:
    """Source files that differ between a reading's commit and `ref`.

    The as-built's own directory is left out: committing a reading makes a new
    commit, and a reading that went stale the moment it was saved would be a
    freshness check that is always red. None when the answer is unknowable --
    no git, or a commit this repository no longer has.
    """
    root = Path(repo).expanduser().resolve()
    if not commit or not is_repo(root):
        return None
    try:
        out = _git(root, "diff", "--name-only", "-z", commit, ref, "--", ".",
                   f":(exclude){AS_BUILT_DIR}/**", f":(exclude){AS_BUILT_DIR}")
    except AsBuiltError:
        return None
    return sorted(p for p in out.decode(errors="replace").split("\0") if p and not _skipped_why(p))


# --------------------------------------------------------------------------
# what the reader is shown
# --------------------------------------------------------------------------


def chunks_of(text: str) -> list[tuple[int, int, str]]:
    """A file in pieces the reader can take, cut between top-level lines.

    A cut prefers a line that starts at the left margin after a blank line --
    where one definition ends and the next begins in nearly every language --
    so a function is rarely split across two calls. Lines are numbered, and the
    numbers are the file's own.
    """
    rows = text.splitlines()
    if len(rows) <= CHUNK_LINES and len(text) <= CHUNK_CHARS:
        return [(1, len(rows), text)]
    out: list[tuple[int, int, str]] = []
    start = 0
    while start < len(rows):
        end = min(len(rows), start + CHUNK_LINES)
        size = sum(len(r) + 1 for r in rows[start:end])
        while end - start > 50 and size > CHUNK_CHARS:
            end -= 50
            size = sum(len(r) + 1 for r in rows[start:end])
        if end < len(rows):
            for cut in range(end, max(start + 1, end - 300), -1):
                line = rows[cut]
                if line and not line[0].isspace() and cut > 0 and not rows[cut - 1].strip():
                    end = cut
                    break
        out.append((start + 1, end, "\n".join(rows[start:end])))
        start = end
    return out


def numbered(first: int, body: str) -> str:
    return "\n".join(f"{first + i:>6}  {line}" for i, line in enumerate(body.splitlines()))


def tree_for(path: str, paths: list[str]) -> str:
    if len(paths) <= TREE_PATHS:
        return "\n".join(paths)
    here = path.rsplit("/", 1)[0] if "/" in path else ""
    near = [p for p in paths if (p.rsplit("/", 1)[0] if "/" in p else "") == here]
    tops = sorted({p.split("/", 1)[0] + ("/" if "/" in p else "") for p in paths})
    return ("Top level:\n" + "\n".join(tops) + f"\n\nBeside this file ({here or 'the root'}):\n"
            + "\n".join(near[:400]) + f"\n\n({len(paths):,} files in all; name any other file by its full path.)")


def batches_of(paths: list[str], sources: dict[str, Source]) -> list[list[str]]:
    """The files to read, in calls: each large file alone, the small ones packed
    in path order -- so a call's files are neighbours, and share what the
    reader is shown of the tree -- up to a size and a count."""
    out: list[list[str]] = []
    group: list[str] = []
    size = 0
    for p in sorted(paths):
        text = sources[p].text
        if len(text) > BATCH_FILE_CHARS or len(chunks_of(text)) > 1:
            out.append([p])
            continue
        if group and (size + len(text) > BATCH_CHARS or len(group) >= BATCH_FILES):
            out.append(group)
            group, size = [], 0
        group.append(p)
        size += len(text)
    if group:
        out.append(group)
    return out


# What a line defines, in most languages, by its shape. Keyword declarations
# count wherever they sit -- a method is a `def` inside a class. A function
# assigned to a name counts at the top level; one inside a body is a local.
# A method inside an object literal counts when the object is one the file
# names (`api = { list: () => ... }`), not one handed to a call
# (`useMutation({ onError: (e) => ... })`), and never a type's signature.
_KEYWORD_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:static\s+)?"
    r"(?:def|class|function\*?|fn|func|fun|sub|struct|enum|module)\s+[A-Za-z_$]")
_NAMED_ARROW = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
_PROPERTY_FN = re.compile(
    r"^\s*[A-Za-z_$][\w$]*\s*:\s*(?:async\s*)?(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)"
    r"(?!\s*(?:void|Promise|string|number|boolean|unknown|any|never)\b)")
_METHOD = re.compile(
    r"^\s*(?:async\s+|static\s+|get\s+|set\s+)*(?!(?:if|for|while|switch|catch|return|else|do|with)\b)"
    r"[A-Za-z_$][\w$]*\s*\([^)]*\)\s*(?::\s*[^{=]+)?\{\s*$")
_OPENS = re.compile(r"[{(\[]\s*$")


def evident_definitions(text: str) -> int:
    """How many things a file plainly defines, by the shape of its lines --
    no language known, so a floor to hold an answer to, not a count."""
    n = 0
    opened: list[tuple[int, str]] = []   # (indent, 'named' | 'call' | 'block') of each open line
    for line in text.splitlines():
        body = line.strip()
        if not body:
            continue
        indent = len(line) - len(line.lstrip())
        while opened and opened[-1][0] >= indent:
            opened.pop()
        inside = opened[-1][1] if opened else "top"
        if _KEYWORD_DEF.match(line) or _NAMED_ARROW.match(line):
            n += 1
        elif _PROPERTY_FN.match(line) and inside == "named" and not body.endswith(";"):
            n += 1
        elif _METHOD.match(line) and inside in ("named", "block"):
            n += 1
        if _OPENS.search(body):
            kind = ("call" if re.search(r"\(\s*\{\s*$|\(\s*$", body) else
                    "named" if re.search(r"(?:=|:)\s*\{\s*$", body) and inside in ("top", "named") else "block")
            opened.append((indent, kind))
    return n


_PATH_LITERAL = re.compile(r"""["'`]/[A-Za-z:{$][^"'`\s]*["'`]""")
#: A router's declaration, in the shape most frameworks share: `path="queue"`,
#: `path: 'trials/:id'`, `path("cards/", view)`.
_ROUTE_DECL = re.compile(r"""\bpath\s*[=:(]\s*\{?\s*["'`]""")
#: Manifests that declare commands or triggers, with what shows they do.
_MANIFESTS = [(re.compile(r"(^|/)package\.json$"), '"scripts"'), (re.compile(r"(^|/)pyproject\.toml$"), "scripts]"),
              (re.compile(r"(^|/)setup\.py$"), "entry_points"), (re.compile(r"(^|/)Cargo\.toml$"), "[[bin]]"),
              (re.compile(r"(^|/)(Makefile|Procfile)$"), ""), (re.compile(r"(^|/)\.github/workflows/[^/]+\.ya?ml$"), "on:")]


def is_manifest(path: str, text: str) -> bool:
    """A manifest that declares commands or triggers: `package.json` scripts,
    a CI workflow, a Makefile."""
    return any(name.search(path) and shows in text for name, shows in _MANIFESTS)


def declares_entries(path: str, text: str) -> bool:
    """Whether a file plainly declares ways in: a manifest's scripts, a CI
    workflow's triggers, or a router's paths."""
    if any(name.search(path) for name, _ in _MANIFESTS):
        return is_manifest(path, text)
    return len(_PATH_LITERAL.findall(text)) + len(_ROUTE_DECL.findall(text)) >= 3


def thin_answer(record: FileRecord, source: Source) -> bool:
    """Whether a batched answer skipped what the file plainly holds. A reader
    answering for ten files at once lists the outer function of a file of
    forty request helpers, and leaves a router's screens and a manifest's
    commands out -- measured on every model tried. A file answered that
    thinly is read again on its own."""
    evident = evident_definitions(source.text)
    if evident >= BATCH_RECHECK_MIN and len(record.functions) < evident * BATCH_RECHECK_SHARE:
        return True
    ways = [w for w in record.ways_in if w.kind != "export"]
    if record.kind not in ("code", "config") or ways:
        return False
    if is_manifest(source.path, source.text):
        return True
    return not record.routes_called and declares_entries(source.path, source.text)


def tree_for_many(batch: list[str], paths: list[str]) -> str:
    """What a call of several files is shown of the tree: all of it when small,
    else the top level and every directory the batch's files sit in."""
    if len(paths) <= TREE_PATHS:
        return "\n".join(paths)
    dirs = sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p in batch})
    tops = sorted({p.split("/", 1)[0] + ("/" if "/" in p else "") for p in paths})
    parts = ["Top level:\n" + "\n".join(tops)]
    for d in dirs:
        near = [p for p in paths if (p.rsplit("/", 1)[0] if "/" in p else "") == d]
        parts.append(f"In {d or 'the root'}:\n" + "\n".join(near[:300]))
    return "\n\n".join(parts) + f"\n\n({len(paths):,} files in all; name any other file by its full path.)"


def batch_prompt(batch: list[str], sources: dict[str, Source], tree: str) -> str:
    body = "\n\n".join(f"# {p}, numbered -- the whole file, {sources[p].lines:,} lines\n\n```\n"
                        f"{numbered(1, sources[p].text)}\n```" for p in batch)
    return (f"# Files\n\nThis call covers {len(batch)} files. Read each one exactly as if it were the only file "
            "you were shown, and give each its own record under its path, copied exactly from its heading. "
            "Never merge two files, and never let one file's contents into another's record.\n\n"
            + "\n".join(f"- {p}" for p in batch)
            + f"\n\n# The repository's files\n\n{tree}\n\n{body}\n")


def reader_prompt(path: str, part: tuple[int, int, str], total_lines: int, n: int, of: int, tree: str) -> str:
    first, last, body = part
    where = (f"The whole file, {total_lines:,} lines." if of == 1 else
             f"Part {n} of {of}: lines {first:,}-{last:,} of {total_lines:,}. Report what THIS part "
             "contains; the other parts are read separately and joined by code.")
    return (f"# File\n\n{path}\n\n{where}\n\n# The repository's files\n\n{tree}\n\n"
            f"# {path}, numbered\n\n```\n{numbered(first, body)}\n```\n")


def merge_records(parts: list[FileRecord]) -> FileRecord:
    """One file's record from the records of its chunks."""
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    merged = FileRecord(purpose=first.purpose, kind=first.kind, is_test=any(p.is_test for p in parts))
    for p in parts:
        for name in ("imports", "launches", "defines", "entities_defined", "entities_read",
                     "entities_written", "outside", "domain_terms"):
            have = getattr(merged, name)
            for x in getattr(p, name):
                if x not in have:
                    have.append(x)
        merged.functions.extend(p.functions)
        merged.ways_in.extend(p.ways_in)
        merged.routes_called.extend(p.routes_called)
    return merged


# --------------------------------------------------------------------------
# the record cache -- a file read once is not read again until it changes
# --------------------------------------------------------------------------


@dataclass
class RecordCache:
    """Records by content, under the project's own evidence directory.

    Keyed by the git blob and by the reader's contract: a new prompt or schema
    is a new question, and an answer to the old one is not reused for it.
    """

    root: Path
    contract: str

    def _file(self, blob: str) -> Path:
        return self.root / f"{blob}-{self.contract}.json"

    def get(self, blob: str) -> FileRecord | None:
        f = self._file(blob)
        if not blob or not f.is_file():
            return None
        try:
            return FileRecord.model_validate_json(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, blob: str, record: FileRecord) -> None:
        if not blob:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self._file(blob).write_text(record.model_dump_json(), encoding="utf-8")


def reader_contract(system_prompt: str) -> str:
    schema = json.dumps(FileRecord.model_json_schema(), sort_keys=True)
    return hashlib.sha256((system_prompt + schema).encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# a reading
# --------------------------------------------------------------------------


@dataclass
class Reading:
    """A finished reading: the computed as-built, and the records behind it."""

    as_built: AsBuilt
    records: dict[str, FileRecord]
    #: The reader contract each record was answered under -- kept with it in
    #: the repository, so a record from an older prompt is not reused.
    contracts: dict[str, str] = field(default_factory=dict)
    read: int = 0
    reused: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)


class AsBuiltReader:
    """Reads one commit of one repository. (INV-6: code decides every step.)"""

    def __init__(self, config: Any, llm: Any, cache: RecordCache | None = None,
                 on_progress: ProgressCallback | None = None, live: dict[str, Any] | None = None) -> None:
        self.config = config
        self.llm = llm
        self.cache = cache
        self.on_progress = on_progress
        self.spent: list[dict[str, Any]] = []
        #: Which agent is working now and how many of its calls are in flight,
        #: read by whoever reports the reading -- the crew screen shows it.
        self.live = live if live is not None else {}
        self.live.setdefault("inflight", {})

    def _flight(self, role: str, delta: int) -> None:
        inflight = self.live.setdefault("inflight", {})
        inflight[role] = max(0, inflight.get(role, 0) + delta)
        if not inflight[role]:
            inflight.pop(role)

    def _emit(self, phase: str, status: str, detail: str = "") -> None:
        if self.on_progress is not None:
            try:
                self.on_progress(phase, status, detail)
            except Exception:
                pass

    def _cost(self, role: str, prompt: str) -> None:
        take = getattr(self.llm, "take_usage", None)
        spent = take(role, prompt) if callable(take) else None
        if isinstance(spent, dict):
            self.spent.append({"role": role, **spent})
        else:
            self.spent.append({"role": role})

    async def read(self, repo: str | Path, commit: str, *, previous: AsBuilt | None = None,
                   previous_records: dict[str, tuple[str, str, FileRecord]] | None = None,
                   project_name: str = "") -> Reading:
        root = Path(repo).expanduser().resolve()
        if not is_repo(root):
            raise AsBuiltError("this project is not a git repository, and an as-built is a reading of a commit")
        sha = head_sha(root, commit)
        if not sha:
            raise AsBuiltError(f"{commit!r} is not a commit in this repository")
        self._emit("listing", "running", f"at {sha[:7]}")
        sources, skipped = sources_at(root, sha)
        records, contracts, read, reused, failed = await self._records(sources, previous_records or {})
        self.previous = previous
        self._emit("joining", "running", f"{len(records):,} files")
        unread = [{"path": f["path"], "why": f["why"]} for f in failed]
        reading = build({p: sources[p] for p in records}, records, commit=sha, built_at=_now(),
                        previous=previous, skipped=skipped, unread=unread)
        await self._describe(reading, records, project_name=project_name)
        reading.cost = self._total_cost()
        return Reading(as_built=reading, records=records, contracts=contracts, read=read, reused=reused,
                       failed=failed)

    async def _records(self, sources: dict[str, Source], known: dict[str, tuple[str, str, FileRecord]]):
        """A record for every file: kept when its content is unchanged and it was
        answered under the reader's current contract, read otherwise."""
        system = self.config.role_prompt("reader")
        contract = reader_contract(system)
        paths = sorted(sources)
        records: dict[str, FileRecord] = {}
        contracts: dict[str, str] = {}
        todo: list[str] = []
        for p in paths:
            src = sources[p]
            hit = self.cache.get(src.blob) if self.cache and self.cache.contract == contract else None
            if hit is None and p in known and known[p][0] == src.blob and known[p][1] == contract:
                hit = known[p][2]
            if hit is not None:
                records[p] = hit
                contracts[p] = contract
            else:
                todo.append(p)
        reused = len(records)
        failed: list[dict[str, str]] = []
        parallel = max(1, int(getattr(self.config.pipeline, "as_built_parallel", 4) or 4))
        semaphore = asyncio.Semaphore(parallel)
        done = 0

        def keep(path: str, record: FileRecord) -> None:
            nonlocal done
            records[path] = record
            contracts[path] = contract
            self.live["done"] = self.live.get("done", 0) + 1
            if self.cache:
                self.cache.put(sources[path].blob, record)
            done += 1
            self._emit("reading", "running", f"{done:,} of {len(todo):,} files read, {reused:,} unchanged")

        # The plan's window, reached: nothing more is asked. What was read is
        # already kept, and the reading stops rather than failing every file
        # left -- which it did, twice, and committed a third of the picture.
        stop: dict[str, Any] = {}
        pause_at = float(getattr(self.config.pipeline, "as_built_pause_at", 0.0) or 0.0)
        route_name = self.config.route_for("reader").name

        def gauge() -> Any:
            """The reader's route's tightest window, as the tool last reported it."""
            try:
                return self.llm.meters.worst(route_name)
            except Exception:  # a stand-in model, or no gauge: nothing to pause on
                return None

        async def ask(prompt: str, schema: type) -> Any:
            async with semaphore:
                if not stop and pause_at > 0:
                    w = gauge()
                    if w is not None and getattr(w, "utilization", 0.0) >= pause_at:
                        stop.setdefault("pause", w)
                if stop:
                    raise _Stopped()
                self._flight("reader", 1)
                try:
                    return await self.llm.ask("reader", prompt, schema, system=system)
                except (RateLimited, RouteExhausted) as exc:
                    stop.setdefault("exc", exc)
                    raise _Stopped() from exc
                finally:
                    self._flight("reader", -1)
                    self._cost("reader", prompt)

        async def one(path: str) -> None:
            src = sources[path]
            pieces = chunks_of(src.text)
            tree = tree_for(path, paths)
            got: list[FileRecord] = []
            for i, piece in enumerate(pieces, 1):
                try:
                    got.append(await ask(reader_prompt(path, piece, src.lines, i, len(pieces), tree), FileRecord))
                except _Stopped:
                    return
                except Exception as exc:  # one file is not the reading
                    failed.append({"path": path, "why": f"the reader failed: {type(exc).__name__}: {exc}"[:300]})
                    return
            keep(path, merge_records(got))

        async def several(batch: list[str]) -> None:
            """Several small files in one call. Code holds the answer to the
            question asked: a record goes to the file it names, the first one
            for a file wins, a path nobody asked about is dropped, and a file
            left without a record is read again on its own."""
            if len(batch) == 1:
                await one(batch[0])
                return
            try:
                answer = await ask(batch_prompt(batch, sources, tree_for_many(batch, paths)), FileRecordBatch)
                given = answer.files if isinstance(answer, FileRecordBatch) else []
            except _Stopped:
                return
            except Exception:
                given = []
            wanted = {p.lower().lstrip("./"): p for p in batch}
            answered: set[str] = set()
            for item in given:
                path = wanted.get((item.path or "").strip().lower().lstrip("./"))
                if path and path not in answered:
                    answered.add(path)
                    if thin_answer(item.record, sources[path]):
                        self.live["reread"] = self.live.get("reread", 0) + 1
                        answered.discard(path)      # read again, alone
                        continue
                    keep(path, item.record)
            await asyncio.gather(*(one(p) for p in batch if p not in answered))

        self.live.update({"todo": len(todo), "done": 0, "reused": reused})
        if todo:
            self._emit("reading", "running", f"0 of {len(todo):,} files read, {reused:,} unchanged")
        await asyncio.gather(*(several(b) for b in batches_of(todo, sources)))
        if stop:
            if "pause" in stop:
                w = stop["pause"]
                wait = w.seconds_to_reset if hasattr(w, "seconds_to_reset") else 0.0
                why = (f"is at {round(w.utilization * 100)}% of its {w.label} window, past the {round(pause_at * 100)}% "
                       "a reading stops at to leave room for your own work")
            else:
                exc = stop["exc"]
                wait = getattr(exc, "retry_after_s", 0.0) or 0.0
                why = "has no credit left" if isinstance(exc, RouteExhausted) else "reached its usage limit"
            when = (f" It reopens at {time.strftime('%H:%M', time.localtime(time.time() + wait))}." if wait else "")
            raise AsBuiltError(
                f"Stopped: the reader's route {why}.{when} {len(records) - reused:,} files were read this time and "
                f"{len(records):,} of {len(paths):,} are kept, so nothing was committed; Read again carries on "
                f"from there, reading only the {len(paths) - len(records):,} left.")
        if not records:
            raise AsBuiltError("no file in this repository could be read")
        return (dict(sorted(records.items())), contracts, len(todo) - len(failed), reused,
                sorted(failed, key=lambda f: f["path"]))

    async def _ask_cartographer(self, prompt: str, schema: type) -> Any:
        system = self.config.role_prompt("cartographer")
        self._flight("cartographer", 1)
        try:
            return await self.llm.ask("cartographer", prompt, schema, system=system)
        finally:
            self._flight("cartographer", -1)
            self._cost("cartographer", prompt)

    async def _describe(self, reading: AsBuilt, records: dict[str, FileRecord], *, project_name: str) -> None:
        """Names for what code grouped, capabilities, and the top of the map.

        A call that fails leaves plain fallback names rather than failing the
        reading: an unnamed group is still a true group.
        """
        files = {f.path: f for f in reading.files}
        unnamed = [s for s in reading.subsystems if not s.name]
        for i in range(0, len(unnamed), NAMES_PER_CALL):
            batch = unnamed[i:i + NAMES_PER_CALL]
            self._emit("describing", "running", "naming subsystems")
            prompt = "# Task\n\nName each subsystem.\n\n" + "\n\n".join(
                f"## key: {s.key}\n{len(s.files)} files, {s.lines:,} lines\n" + "\n".join(
                    f"- {p} ({files[p].lines:,} lines): {files[p].purpose}" for p in s.files[:40])
                + (f"\n- ... and {len(s.files) - 40} more" if len(s.files) > 40 else "")
                for s in batch)
            try:
                naming = await self._ask_cartographer(prompt, GroupNaming)
            except Exception:
                naming = GroupNaming()
            apply_names(reading, naming, what="subsystem")
        apply_names(reading, GroupNaming(), what="subsystem")

        by_file: dict[str, list] = {}
        for p in reading.parts:
            by_file.setdefault(p.file, []).append(p)
        for path, parts in sorted(by_file.items()):
            if all(p.name for p in parts):
                continue
            self._emit("describing", "running", f"naming the parts of {path}")
            node = files[path]
            prompt = (f"# Task\n\nName each part of {path}. The file is {node.lines:,} lines: {node.purpose}\n"
                      "Each part is a group of its functions that call each other more than the rest of the "
                      "file. Name it by what those functions do together.\n\n" + "\n\n".join(
                          f"## key: {p.key}\n{len(p.functions)} functions, {p.lines:,} lines\n"
                          + ", ".join(p.functions[:40]) for p in parts))
            try:
                naming = await self._ask_cartographer(prompt, GroupNaming)
            except Exception:
                naming = GroupNaming()
            apply_names(reading, naming, what="part", file=path)
        # a part's name can make a finding read better, so they are redone
        from .asbuilt_graph import findings as _findings
        reading.findings = _findings(reading)

        carried = carry_capabilities(reading, self.previous) if getattr(self, "previous", None) else None
        if carried is not None:
            apply_capabilities(reading, records, carried)
        elif reading.ways_in:
            self._emit("describing", "running", "grouping capabilities")
            prompt = ("# Task\n\nGroup these ways in to the system into capabilities -- what a person can do "
                      "with it. Every id goes in exactly one capability, or in not_capabilities.\n\n"
                      + ("Codes already taken by subsystems: " + ", ".join(s.code for s in reading.subsystems if s.code)
                         + ".\n\n" if any(s.code for s in reading.subsystems) else "")
                      + "\n".join(f"- id: {w.id} -- in {w.file}"
                                  + (f", handled by {w.handler}" if w.handler else "")
                                  + f" ({files[w.file].purpose[:160]})" for w in reading.ways_in))
            try:
                capmap = await self._ask_cartographer(prompt, CapabilityMap)
            except Exception:
                capmap = CapabilityMap()
            apply_capabilities(reading, records, capmap)

        if not reading.summary:
            self._emit("describing", "running", "writing the system summary")
            subs = "\n".join(f"- {s.name} ({len(s.files)} files, {s.lines:,} lines): {s.summary}"
                             for s in reading.subsystems)
            caps = "\n".join(f"- {c.name}: {c.summary}" for c in reading.capabilities[:40])
            biggest = sorted((f for f in reading.files if not f.is_test), key=lambda f: -f.lines)[:60]
            prompt = ("# Task\n\nDescribe this system for a developer arriving cold"
                      + (f". It is called {project_name}" if project_name else "") + ".\n\n"
                      f"## Subsystems\n{subs}\n\n## Shared by nearly everything\n"
                      + "\n".join(f"- {h}: {files[h].purpose}" for h in reading.hubs)
                      + f"\n\n## Capabilities\n{caps or '(none found)'}\n\n## Files to choose from\n"
                      + "\n".join(f"- {f.path} ({f.lines:,} lines): {f.purpose}" for f in biggest))
            try:
                account = await self._ask_cartographer(prompt, SystemAccount)
                apply_account(reading, account)
            except Exception:
                pass
        # Every subsystem and capability gets its three letters, and the
        # findings are said again with them.
        assign_codes(reading, getattr(self, "previous", None))
        from .asbuilt_graph import findings as _again
        reading.findings = _again(reading)

        # How it runs: asked once, and again only when the question changes --
        # other subsystems, a mention nobody sorted, a route nobody calls.
        account = carry_architecture(reading, getattr(self, "previous", None))
        if account is None and reading.subsystems:
            self._emit("describing", "running", "drawing how it runs")
            try:
                account = await self._ask_cartographer(architecture_prompt(reading), ArchitectureAccount)
            except Exception:
                account = None
        # Like every name here, an answer that does not hold leaves the reading
        # without it rather than without a reading.
        if isinstance(account, ArchitectureAccount):
            try:
                apply_architecture(reading, account)
            except Exception:
                reading.architecture = reading.architecture_account = None

    def _total_cost(self) -> dict[str, Any]:
        """What the reading cost, in all and by agent -- the crew screen adds
        these to each agent's calls, which a reading otherwise never reaches."""
        keys = ("cost_usd", "notional_usd", "prompt_tokens", "completion_tokens", "total_tokens")

        def add(rows: list[dict[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {"calls": len(rows),
                                   "unbilled_calls": sum(1 for r in rows if r.get("notional_usd"))}
            for key in keys:
                vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
                if vals:
                    out[key] = round(sum(vals), 6) if any(isinstance(v, float) for v in vals) else sum(vals)
            return out

        total = add(self.spent)
        roles = sorted({s["role"] for s in self.spent})
        total["by_role"] = {r: add([s for s in self.spent if s["role"] == r]) for r in roles}
        return total


# --------------------------------------------------------------------------
# where a reading is kept
# --------------------------------------------------------------------------


def load_committed(repo: str | Path, ref: str = "HEAD", *,
                   with_records: bool = True) -> tuple[AsBuilt | None, dict[str, tuple[str, str, FileRecord]]]:
    """The as-built committed at `ref`, and its per-file records -- each with
    the blob it was read from and the reader contract it was answered under."""
    root = Path(repo).expanduser().resolve()
    try:
        raw = _git(root, "show", f"{ref}:{AS_BUILT_DIR}/graph.json", timeout=60)
    except (AsBuiltError, OSError, subprocess.SubprocessError):
        return None, {}
    try:
        reading = AsBuilt.model_validate_json(raw)
    except ValueError:
        return None, {}
    if reading.version > AS_BUILT_VERSION:
        return None, {}
    records: dict[str, tuple[str, str, FileRecord]] = {}
    if not with_records:
        return reading, records
    try:
        listing = _git(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", f"{AS_BUILT_DIR}/records")
        names = [n for n in listing.decode(errors="replace").split("\0") if n.endswith(".json")]
        if names:
            batch = _git(root, "cat-file", "--batch",
                         input_bytes="".join(f"{ref}:{n}\n" for n in names).encode(), timeout=300)
            pos = 0
            prefix = f"{AS_BUILT_DIR}/records/"
            for n in names:
                header_end = batch.index(b"\n", pos)
                header = batch[pos:header_end].split()
                size = int(header[2]) if len(header) >= 3 and header[2].isdigit() else 0
                body = batch[header_end + 1: header_end + 1 + size]
                pos = header_end + 1 + size + 1
                path = n[len(prefix):-len(".json")]
                try:
                    kept = json.loads(body)
                    records[path] = (str(kept.get("blob", "")), str(kept.get("contract", "")),
                                     FileRecord.model_validate(kept.get("record", {})))
                except (ValueError, AttributeError):
                    continue
    except (AsBuiltError, ValueError):
        pass
    return reading, records


_COVERAGE: dict[tuple[str, str, str, float], dict[str, int]] = {}


def cache_coverage(repo: str | Path, cache_dir: Path, contract: str) -> dict[str, int]:
    """How many of the files at the checked-out commit already have a record
    under the reader's current contract -- what a reading that paused, or
    stopped at the plan's limit, kept. Worked out once per commit and per
    state of the cache, not on every look at the tab."""
    root = Path(repo).expanduser().resolve()
    sha = head_sha(root, "HEAD")
    stamp = cache_dir.stat().st_mtime if cache_dir.is_dir() else 0.0
    key = (str(root), sha, contract, stamp)
    if key not in _COVERAGE:
        sources, _ = sources_at(root, sha)
        cache = RecordCache(cache_dir, contract)
        kept = sum(1 for s in sources.values() if s.blob and cache._file(s.blob).is_file())
        while len(_COVERAGE) >= 8:
            _COVERAGE.pop(next(iter(_COVERAGE)))
        _COVERAGE[key] = {"kept": kept, "listed": len(sources)}
    return _COVERAGE[key]


#: The last few committed readings with their records, by the blob of their
#: graph -- reading a file's source should not reload every record each time.
_READINGS: dict[tuple[str, str], tuple[AsBuilt, dict[str, FileRecord]]] = {}


def source_view(repo: str | Path, path: str, ref: str = "HEAD") -> dict | None:
    """One file as the committed reading read it -- the very blob -- with what
    the reading knows about its lines. None when nothing is committed; a
    KeyError when the reading has no such file."""
    root = Path(repo).expanduser().resolve()
    try:
        graph_blob = _git(root, "rev-parse", f"{ref}:{AS_BUILT_DIR}/graph.json", timeout=30).decode().strip()
    except (AsBuiltError, OSError, subprocess.SubprocessError):
        return None
    key = (str(root), graph_blob)
    if key not in _READINGS:
        reading, kept = load_committed(root, ref)
        if reading is None:
            return None
        while len(_READINGS) >= 4:
            _READINGS.pop(next(iter(_READINGS)))
        _READINGS[key] = (reading, {p: rec for p, (_, _, rec) in kept.items()})
    reading, records = _READINGS[key]
    node = reading.file(path)
    if node is None:
        raise KeyError(path)
    raw = b""
    for spec in ([node.blob] if node.blob else []) + ([f"{reading.commit}:{path}"] if reading.commit else []):
        try:
            raw = _git(root, "cat-file", "-p", spec, timeout=30)
            break
        except (AsBuiltError, OSError, subprocess.SubprocessError):
            continue
    text = raw.decode("utf-8", errors="replace")
    try:
        now = _git(root, "rev-parse", f"{ref}:{path}", timeout=30).decode().strip()
    except (AsBuiltError, OSError, subprocess.SubprocessError):
        now = ""
    return {"path": path, "commit": reading.commit, "blob": node.blob, "found": bool(raw),
            "changed_since": bool(node.blob) and now != node.blob, "text": text,
            **source_notes(reading, records, path, text)}


def load_file(path: Path) -> AsBuilt | None:
    try:
        reading = AsBuilt.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return reading if reading.version <= AS_BUILT_VERSION else None


def uncommitted_as_built(repo: str | Path) -> list[str]:
    """Changes in the working copy under `.fabrika/as-built/` that are not committed."""
    root = Path(repo).expanduser().resolve()
    try:
        out = _git(root, "status", "--porcelain", "-z", "--", AS_BUILT_DIR)
    except AsBuiltError:
        return []
    return [e[3:] for e in out.decode(errors="replace").split("\0") if len(e) > 3]


def write_into(target: Path, reading: Reading, *, project_name: str = "") -> list[str]:
    """Replace everything under `target` with this reading's pages. Returns the paths written."""
    from .asbuilt_pages import render

    pages = render(reading.as_built, reading.records, project_name=project_name, contracts=reading.contracts)
    if target.exists():
        shutil.rmtree(target)
    written: list[str] = []
    for rel, text in sorted(pages.items()):
        f = target / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        written.append(rel)
    return written


def commit_into_repo(repo: str | Path, reading: Reading, *, project_name: str = "") -> dict[str, Any]:
    """Write the as-built into the repository and commit exactly those paths.

    On a person's press, like `.fabrika/Dockerfile`: the working copy is theirs,
    so an uncommitted edit under the as-built's directory stops the write
    rather than being overwritten, and nothing else they have staged is swept
    into the commit.
    """
    root = Path(repo).expanduser().resolve()
    dirty = uncommitted_as_built(root)
    if dirty:
        raise AsBuiltError(f"{AS_BUILT_DIR}/ has changes that are not committed ({dirty[0]}"
                           + (f" and {len(dirty) - 1} more" if len(dirty) > 1 else "")
                           + "). Commit or discard them first, so nothing of yours is overwritten.")
    target = root / AS_BUILT_DIR
    written = write_into(target, reading, project_name=project_name)
    sha = reading.as_built.commit
    subs = len(reading.as_built.subsystems)
    commit, problem = commit_paths(
        root, [AS_BUILT_DIR],
        f"As-built at {sha[:7]}: {subs} subsystem{'s' if subs != 1 else ''}, "
        f"{len(reading.as_built.files):,} files\n\n"
        "Generated by Fabrika from the commit named above: what the code is, how it\n"
        "fits together and what it does. Regenerate it rather than editing it.")
    return {"path": AS_BUILT_DIR, "files": len(written), "commit": commit, "commit_problem": problem}


def status(repo: str | Path, ref: str = "HEAD") -> dict[str, Any]:
    """Whether the committed as-built describes `ref`, for the console."""
    reading, _ = load_committed(repo, ref, with_records=False)
    if reading is None:
        return {"exists": False, "fresh": False, "commit": "", "changed": []}
    changed = changed_since(repo, reading.commit, ref)
    return {"exists": True, "commit": reading.commit, "built_at": reading.built_at,
            "fresh": changed == [], "changed": (changed or [])[:200],
            "changed_count": len(changed) if changed is not None else None}


# --------------------------------------------------------------------------
# a project's as-built
# --------------------------------------------------------------------------


class ProjectAsBuilt:
    """Readings of one registered project, on a person's press or for a feature.

    Two kinds, and they end in different places. `refresh` is a person asking
    for the as-built of what is checked out: it is read, written under
    `.fabrika/as-built/` and committed, and it is what the repository carries.
    `read_for_feature` is a reading at one of a feature's commits -- the one it
    branched from, or its head at review -- kept in that feature's ledger to
    say what the feature changed in the system. It never touches the checkout.

    One reading per project at a time: two would read the same files twice
    and race to write the same directory.
    """

    #: project id -> what its reading is doing now. Process-wide, like the
    #: progress hub it reports through.
    running: dict[str, dict[str, Any]] = {}
    #: A feature's readings, which run quietly beside it -- by
    #: "<project>/<feature>/<label>". Kept apart so the project's own tab never
    #: reads a feature's reading as its own, and the crew screen sees both.
    background: dict[str, dict[str, Any]] = {}

    def __init__(self, config: Any, llm: Any, on_progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.config = config
        self.llm = llm
        self.on_progress = on_progress

    def _emitter(self, project: Any, live: dict[str, Any] | None = None) -> ProgressCallback:
        def emit(phase: str, status: str, detail: str = "") -> None:
            self.running[project.id] = {"phase": phase, "detail": detail, "at": _now(),
                                        "role": _role_of(phase), "live": live if live is not None else {}}
            if self.on_progress is None:
                return
            try:
                self.on_progress({"project_id": project.id, "feature_id": "", "stage": project.state.stage,
                                  "phase": "as-built", "step": phase, "status": status, "detail": detail,
                                  "error": "", "at": _now()})
            except Exception:
                pass
        return emit

    def _reader(self, project: Any, emit: ProgressCallback | None,
                live: dict[str, Any] | None = None) -> AsBuiltReader:
        cache = RecordCache(project.store.artifact_dir("as-built-cache"),
                            reader_contract(self.config.role_prompt("reader")))
        return AsBuiltReader(self.config, self.llm, cache=cache, on_progress=emit, live=live)

    def busy(self, project: Any) -> bool:
        return project.id in self.running

    def _finish(self, project: Any, status: str, detail: str) -> None:
        self.running.pop(project.id, None)
        if self.on_progress is None:
            return
        try:
            self.on_progress({"project_id": project.id, "feature_id": "", "stage": project.state.stage,
                              "phase": "as-built", "step": status, "status": status, "detail": detail,
                              "error": detail if status == "failed" else "", "at": _now()})
        except Exception:
            pass

    async def refresh(self, project: Any) -> dict[str, Any]:
        """Read what is checked out, and commit its as-built into the repository."""
        if self.busy(project):
            raise AsBuiltError("a reading of this project is already running")
        live: dict[str, Any] = {}
        emit = self._emitter(project, live)
        try:
            emit("starting", "running", "")
            previous, known = load_committed(project.repo_path, "HEAD")
            reader = self._reader(project, emit, live)
            reading = await reader.read(project.repo_path, "HEAD", previous=previous,
                                        previous_records=known, project_name=project.state.name)
            emit("writing", "running", AS_BUILT_DIR)
            wrote = commit_into_repo(project.repo_path, reading, project_name=project.state.name)
        except Exception as exc:
            message = str(exc) if isinstance(exc, AsBuiltError) else f"{type(exc).__name__}: {exc}"
            project.store.append("as_built_failed", {"error": message[:500]}, role="orchestrator")
            self._finish(project, "failed", message[:200])
            raise
        summary = {
            "commit": reading.as_built.commit, "files": len(reading.as_built.files),
            "subsystems": len(reading.as_built.subsystems), "parts": len(reading.as_built.parts),
            "capabilities": len(reading.as_built.capabilities), "read": reading.read,
            "reused": reading.reused, "failed": reading.failed, "pages": wrote["files"],
            "commit_sha": wrote["commit"], "commit_problem": wrote["commit_problem"], "path": wrote["path"],
        }
        project.store.append("as_built", summary, role="human", meta=reading.as_built.cost)
        self._finish(project, "done", f"{reading.read:,} read, {reading.reused:,} unchanged")
        return summary

    async def read_for_feature(self, project: Any, store: Any, commit: str, label: str) -> AsBuilt | None:
        """A reading at one of a feature's commits, kept in its ledger. None on failure.

        A failure here never fails the feature: the as-built is an account of
        the system, not a check on the change.
        """
        key = f"{project.id}/{store.feature_id}/{label}"
        live: dict[str, Any] = {}

        def emit(phase: str, status: str, detail: str = "") -> None:
            self.background[key] = {"project": project.id, "feature": store.feature_id, "label": label,
                                    "phase": phase, "detail": detail, "role": _role_of(phase), "live": live,
                                    "at": _now()}

        try:
            emit("starting", "running")
            base, known = load_committed(project.repo_path, "HEAD")
            previous = latest_feature_reading(store) or base
            reader = self._reader(project, emit, live)
            reading = await reader.read(project.repo_path, commit, previous=previous, previous_records=known,
                                        project_name=project.state.name)
        except Exception as exc:
            store.append("as_built_failed", {"label": label, "commit": commit,
                                             "error": f"{type(exc).__name__}: {exc}"[:500]}, role="orchestrator")
            return None
        finally:
            self.background.pop(key, None)
        target = store.artifact_dir("as-built") / f"{label}-{reading.as_built.commit[:12]}.json"
        target.write_text(reading.as_built.model_dump_json(), encoding="utf-8")
        store.append("as_built", {"label": label, "commit": reading.as_built.commit,
                                  "file": str(target.relative_to(store.dir)), "files": len(reading.as_built.files),
                                  "read": reading.read, "reused": reading.reused},
                     role="orchestrator", meta=reading.as_built.cost)
        return reading.as_built


def _role_of(phase: str) -> str:
    """Which agent a step of a reading belongs to; empty where code does it."""
    return {"reading": "reader", "describing": "cartographer"}.get(phase, "")


def now_running() -> list[dict[str, Any]]:
    """Every reading in this process and what it is doing, for the crew screen."""
    out = []
    for pid, r in ProjectAsBuilt.running.items():
        out.append({"project": pid, "feature": "", "label": "refresh", **_public(r)})
    for r in ProjectAsBuilt.background.values():
        out.append({"project": r["project"], "feature": r["feature"], "label": r["label"], **_public(r)})
    return out


def _public(r: dict[str, Any]) -> dict[str, Any]:
    live = r.get("live") or {}
    return {"phase": r.get("phase", ""), "detail": r.get("detail", ""), "role": r.get("role", ""),
            "inflight": dict(live.get("inflight") or {}), "done": live.get("done"), "todo": live.get("todo")}


def feature_reading(store: Any, label: str) -> AsBuilt | None:
    """The latest reading with this label in a feature's ledger."""
    found = None
    for r in store.all("as_built"):
        if (r.get("payload") or {}).get("label") == label:
            found = r
    if not found:
        return None
    return load_file(store.dir / found["payload"]["file"])


def latest_feature_reading(store: Any) -> AsBuilt | None:
    records = store.all("as_built")
    if not records:
        return None
    return load_file(store.dir / records[-1]["payload"]["file"])
