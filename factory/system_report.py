"""What a feature changed in the system, split by what caused it.

A feature's as-built is read twice -- at the commit it branched from and at its
head -- and `asbuilt_graph.system_change` lists every difference between the
two readings. Not every difference is the feature's. Groups and capabilities
are names a model gives the code each time it reads it, so a second reading can
merge two groups, rename a capability or count an old export as a way in for
the first time, on code nobody touched. Shown as one list, that reads as a
feature that reorganised the system when it added three routes.

So each difference is attributed here, by code and from facts a person could
check: which files the branch added or changed (their blobs), whether a symbol
was already in a file at the base commit (git), and what the lockfiles say the
branch added (the Dependencies record). What the branch caused is the report;
what moved because the code was read again is kept, beside it and labelled.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .asbuilt_graph import AsBuilt, Capability, Subsystem, WayInNode, labelled, system_change, untested_share

#: `read(commit, path)` -> the file's text at that commit, or None where it did not exist.
Reader = Callable[[str, str], "str | None"]


class FileMark(BaseModel):
    path: str
    status: Literal["added", "changed", "same"]


class GroupView(BaseModel):
    """One of the head reading's groups, its files marked by what the branch did."""

    key: str
    code: str = ""
    name: str = ""
    files: list[FileMark] = Field(default_factory=list)
    touched: bool = False
    was: list[str] = Field(default_factory=list, description="The base reading's groups it continues.")
    lineage: Literal["same", "renamed", "merged", "new"] = "same"


class WayView(BaseModel):
    id: str
    kind: str
    name: str
    method: str = ""
    file: str = ""
    test: bool = False


class CapabilityView(BaseModel):
    code: str = ""
    name: str
    summary: str = ""
    ways_in: list[str] = Field(default_factory=list)


class EntityView(BaseModel):
    name: str
    file: str = ""
    test: bool = False


class HealthRow(BaseModel):
    """One of the reading's findings, before and after."""

    kind: str
    title: str
    before: str = ""
    after: str = ""
    direction: Literal["better", "worse", "same", "new", "gone"] = "same"
    detail: str = ""


class Move(BaseModel):
    path: str
    before: str
    after: str
    touched: bool = False


class SystemReport(BaseModel):
    base: str = ""
    head: str = ""
    files_added: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    files_removed: list[str] = Field(default_factory=list)

    # -- caused by the branch -------------------------------------------------
    capabilities_added: list[CapabilityView] = Field(default_factory=list)
    capabilities_removed: list[CapabilityView] = Field(default_factory=list)
    ways_in_added: list[WayView] = Field(default_factory=list)
    ways_in_removed: list[WayView] = Field(default_factory=list)
    entities_added: list[EntityView] = Field(default_factory=list)
    packages_added: list[str] | None = Field(default=None, description=(
        "Direct packages the lockfiles say this branch added. None: the lockfiles were not read."))
    groups: list[GroupView] = Field(default_factory=list)
    loose: list[FileMark] = Field(default_factory=list, description=(
        "Code the branch added or changed that no group holds -- a hub every group leans on, say."))
    tests_touched: int = 0
    health: list[HealthRow] = Field(default_factory=list)

    # -- moved because the code was read again ------------------------------
    moves: list[Move] = Field(default_factory=list)
    capabilities_before: list[str] = Field(default_factory=list)
    capabilities_after: list[str] = Field(default_factory=list)
    ways_in_recounted: list[str] = Field(default_factory=list)
    ways_in_uncounted: list[str] = Field(default_factory=list)
    entities_recounted: list[str] = Field(default_factory=list)
    outside_named: list[str] = Field(default_factory=list)

    def product_ways_added(self) -> list[WayView]:
        return [w for w in self.ways_in_added if not w.test]

    def reading_moved(self) -> int:
        """How many things the second reading moved without the branch moving them."""
        renamed = sum(1 for g in self.groups if g.lineage in ("renamed", "merged"))
        return (len(self.moves) + renamed + len(self.ways_in_recounted) + len(self.ways_in_uncounted)
                + len(self.entities_recounted) + len(self.outside_named))

    def lines(self) -> list[str]:
        """The change in sentences, for the packet on the branch: what the
        branch caused first, and what the second reading moved in one line."""
        out: list[str] = []
        bits = []
        if self.files_added:
            bits.append(f"{len(self.files_added)} new file{_s(len(self.files_added))}")
        if self.files_changed:
            bits.append(f"{len(self.files_changed)} changed")
        if self.files_removed:
            bits.append(f"{len(self.files_removed)} removed")
        if bits:
            out.append("Files: " + ", ".join(bits) + ".")
        for c in self.capabilities_added:
            out.append(f"New capability: {labelled(c.name, c.code)}"
                       + (f" -- {c.summary.rstrip('.')}." if c.summary else "."))
        for c in self.capabilities_removed:
            out.append(f"Capability gone: {labelled(c.name, c.code)}.")
        product = self.product_ways_added()
        if product:
            out.append("New ways in: " + ", ".join(_way_text(w) for w in product) + ".")
        tests = [w for w in self.ways_in_added if w.test]
        if tests:
            out.append(f"New test helpers: {', '.join(w.name for w in tests)}.")
        if self.ways_in_removed:
            out.append("Ways in removed: " + ", ".join(_way_text(w) for w in self.ways_in_removed) + ".")
        stored = [e.name for e in self.entities_added if not e.test]
        if stored:
            out.append("New data entities: " + ", ".join(stored) + ".")
        if self.packages_added:
            out.append("New packages: " + ", ".join(self.packages_added) + ".")
        landed = [labelled(g.name or g.key, g.code) for g in self.groups if g.touched]
        if landed:
            out.append("Lands in: " + ", ".join(landed) + ".")
        for h in self.health:
            if h.direction in ("better", "worse", "new", "gone"):
                change = f"{h.before} -> {h.after}" if h.before and h.after else (h.after or h.before)
                out.append(f"{h.title} ({h.direction}{': ' + change if change else ''}).")
        moved = self.reading_moved()
        if moved:
            out.append(f"Not this branch: {moved} thing{_s(moved)} moved only because the code was read "
                       "again -- groups regrouped or renamed, ways in and outside systems counted differently.")
        return out


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _way_text(w: WayView) -> str:
    return f"{w.method} {w.name}".strip() if w.kind == "route" else f"{w.name} ({w.kind})"


# --------------------------------------------------------------------------
# reading files at a commit
# --------------------------------------------------------------------------


def git_reader(repo: str | Path) -> Reader:
    """Files at a commit, from git's objects rather than any working copy."""

    @lru_cache(maxsize=512)
    def read(commit: str, path: str) -> str | None:
        if not commit:
            return None
        try:
            out = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=str(repo),
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    return read


def _names(text: str | None, symbol: str) -> bool:
    if not text or not symbol:
        return False
    return re.search(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])", text) is not None


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def report(base: AsBuilt, head: AsBuilt, read: Reader, dependencies: dict[str, Any] | None = None) -> SystemReport:
    """Every difference between two readings, attributed to the branch or to the reading."""
    change = system_change(base, head)
    out = SystemReport(base=base.commit, head=head.commit, files_added=change.files_added,
                       files_changed=change.files_changed, files_removed=change.files_removed)
    added, changed = set(change.files_added), set(change.files_changed)
    touched = added | changed
    bfiles = {f.path: f for f in base.files}
    hfiles = {f.path: f for f in head.files}

    def is_test(path: str) -> bool:
        node = hfiles.get(path) or bfiles.get(path)
        return bool(node and node.is_test)

    # Groups, and which of the base reading's groups each one continues.
    lineage = _lineage(base.subsystems, head.subsystems)
    blabel = {s.key: labelled(s.name or s.key, s.code) for s in base.subsystems}
    hlabel = {s.key: labelled(s.name or s.key, s.code) for s in head.subsystems}
    for s in head.subsystems:
        marks = [FileMark(path=p, status="added" if p in added else "changed" if p in changed else "same")
                 for p in s.files]
        marks.sort(key=lambda m: (["added", "changed", "same"].index(m.status), m.path))
        was = [k for k, h in lineage.items() if h == s.key]
        if not was:
            kind = "new"
        elif len(was) > 1:
            kind = "merged"
        else:
            b = next(x for x in base.subsystems if x.key == was[0])
            kind = "same" if (b.name, b.code) == (s.name, s.code) else "renamed"
        out.groups.append(GroupView(key=s.key, code=s.code, name=s.name, files=marks,
                                    touched=any(m.status != "same" for m in marks),
                                    was=[blabel[k] for k in sorted(was)], lineage=kind))
    out.groups.sort(key=lambda g: (not g.touched, -sum(m.status != "same" for m in g.files), g.code or g.key))
    grouped = {p for s in head.subsystems for p in s.files}
    out.loose = [FileMark(path=p, status="added" if p in added else "changed")
                 for p in sorted(touched) if p not in grouped and not is_test(p)
                 and (hfiles.get(p) or bfiles[p]).kind == "code"]
    out.tests_touched = sum(1 for p in touched if is_test(p))

    # A file that kept its place moved nowhere, whatever its group is called
    # now. One that left the group its old group became is a move.
    for path in sorted(set(bfiles) & set(hfiles)):
        b, h = bfiles[path].subsystem, hfiles[path].subsystem
        if b and h and lineage.get(b) != h:
            out.moves.append(Move(path=path, before=blabel.get(b, b), after=hlabel.get(h, h), touched=path in touched))

    # Ways in: the branch's only when the symbol is not in the file at the base.
    bways = {w.id: w for w in base.ways_in}
    hways = {w.id: w for w in head.ways_in}
    for wid in change.ways_in_added:
        w = hways[wid]
        if _way_is_new(w, base.commit, added, changed, read):
            out.ways_in_added.append(_view(w, is_test(w.file)))
        else:
            out.ways_in_recounted.append(wid)
    for wid in change.ways_in_removed:
        w = bways[wid]
        if _way_is_gone(w, head.commit, change.files_removed, read):
            out.ways_in_removed.append(_view(w, is_test(w.file)))
        else:
            out.ways_in_uncounted.append(wid)

    # Capabilities: new when every way into it is new; the rest are the same
    # behaviour named again.
    new_ways = {w.id for w in out.ways_in_added}
    gone_ways = {w.id for w in out.ways_in_removed}
    out.capabilities_added = [_cap(c) for c in head.capabilities if c.ways_in and set(c.ways_in) <= new_ways]
    out.capabilities_removed = [_cap(c) for c in base.capabilities if c.ways_in and set(c.ways_in) <= gone_ways]
    kept_new = {c.name for c in out.capabilities_added}
    kept_gone = {c.name for c in out.capabilities_removed}
    if {c.name for c in base.capabilities} - kept_gone != {c.name for c in head.capabilities} - kept_new:
        out.capabilities_before = [labelled(c.name, c.code) for c in base.capabilities if c.name not in kept_gone]
        out.capabilities_after = [labelled(c.name, c.code) for c in head.capabilities if c.name not in kept_new]

    # Entities: the branch's when the defining file is new, or did not name it before.
    defined_in = {e: f.path for f in head.files for e in f.entities_defined}
    for name in change.entities_added:
        path = defined_in.get(name, "")
        if path in added or not _names(read(base.commit, path), name):
            out.entities_added.append(EntityView(name=name, file=path, test=is_test(path)))
        else:
            out.entities_recounted.append(name)

    # Outside systems: the branch's only if the lockfiles say it brought one in.
    out.packages_added = _packages_added(dependencies)
    for name in change.outside_added:
        if out.packages_added and any(_same_thing(name, p) for p in out.packages_added):
            continue
        out.outside_named.append(name)

    out.health = _health(base, head, lineage)
    return out


def _lineage(before: list[Subsystem], after: list[Subsystem]) -> dict[str, str]:
    """Each base group -> the head group holding most of its files. A group the
    branch left alone keeps its files, so it maps onto itself under whatever
    name the second reading gave it."""
    where = {p: s.key for s in after for p in s.files}
    out: dict[str, str] = {}
    for b in before:
        votes: dict[str, int] = defaultdict(int)
        for p in b.files:
            if p in where:
                votes[where[p]] += 1
        if votes:
            out[b.key] = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0] != b.key, kv[0]))[0][0]
    return out


def _symbol(w: WayInNode) -> str:
    return w.handler or ("" if w.kind == "route" else w.name)


def _way_is_new(w: WayInNode, base: str, added: set[str], changed: set[str], read: Reader) -> bool:
    if w.file in added:
        return True
    symbol = _symbol(w)
    if not symbol:
        # A route with no handler named: only the file can say, and an
        # untouched file cannot have grown one.
        return w.file in changed
    return not _names(read(base, w.file), symbol)


def _way_is_gone(w: WayInNode, head: str, removed: Iterable[str], read: Reader) -> bool:
    if w.file in set(removed):
        return True
    symbol = _symbol(w)
    if not symbol:
        return False
    return not _names(read(head, w.file), symbol)


def _view(w: WayInNode, test: bool) -> WayView:
    return WayView(id=w.id, kind=w.kind, name=w.name, method=w.method, file=w.file, test=test)


def _cap(c: Capability) -> CapabilityView:
    return CapabilityView(code=c.code, name=c.name, summary=c.summary, ways_in=list(c.ways_in))


def _packages_added(dependencies: dict[str, Any] | None) -> list[str] | None:
    if not dependencies:
        return None
    return sorted({c.get("name", "") for c in dependencies.get("changes") or []
                   if c.get("kind") == "added" and c.get("direct") and c.get("name")})


def _same_thing(outside: str, package: str) -> bool:
    flat = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    o, p = flat(outside), flat(package)
    return bool(p) and (p in o or o in p)


def _pairs(reading: AsBuilt, rename: dict[str, str] | None = None) -> dict[tuple[str, str], int]:
    nodes = {f.path: f for f in reading.files}
    out: dict[tuple[str, str], int] = defaultdict(int)
    for e in reading.edges:
        a, b = nodes[e.source].subsystem, nodes[e.target].subsystem
        if rename is not None:
            a, b = rename.get(a, ""), rename.get(b, "")
        if a and b and a != b:
            out[(a, b)] += 1
    return out


def _health(base: AsBuilt, head: AsBuilt, lineage: dict[str, str]) -> list[HealthRow]:
    """The reading's findings, before and after, counted again rather than
    read off their titles -- and groups compared through their lineage, so a
    tangle that was renamed is the same tangle."""
    rows: list[HealthRow] = []
    hlabel = {s.key: labelled(s.name or s.key, s.code) for s in head.subsystems}

    if any(f.is_test for f in base.files) and any(f.is_test for f in head.files):
        b, _ = untested_share(base)
        h, still = untested_share(head)
        bp, hp = round(b * 100), round(h * 100)
        rows.append(HealthRow(
            kind="untested", title="Code no test reaches",
            before=f"{bp}%", after=f"{hp}%",
            direction="same" if bp == hp else "better" if hp < bp else "worse",
            detail=("Still untested: " + ", ".join(f.path for f in sorted(still, key=lambda f: -f.lines)[:4]) + ".")
            if still else "Every file of 50 lines or more is reached by a test."))

    before, after = _pairs(base, lineage), _pairs(head)
    for a, b in sorted({(min(x, y), max(x, y)) for x, y in after if (y, x) in after}
                       | {(min(x, y), max(x, y)) for x, y in before if (y, x) in before}):
        was = before.get((a, b), 0) + before.get((b, a), 0) if (b, a) in before and (a, b) in before else 0
        now = after.get((a, b), 0) + after.get((b, a), 0) if (b, a) in after and (a, b) in after else 0
        title = f"{hlabel.get(a, a)} and {hlabel.get(b, b)} depend on each other"
        if was and now:
            direction = "same" if was == now else "better" if now < was else "worse"
        else:
            direction = "new" if now else "gone"
        rows.append(HealthRow(kind="mutual", title=title, before=f"{was} links" if was else "",
                              after=f"{now} links" if now else "", direction=direction,
                              detail=f"{after.get((a, b), 0)} links one way, {after.get((b, a), 0)} the other." if now
                              else "No longer two-way."))

    seen = {"untested", "mutual"}
    btitles: dict[str, list[str]] = defaultdict(list)
    htitles: dict[str, list[str]] = defaultdict(list)
    for f in base.findings:
        btitles[f.kind].append(f.title)
    for f in head.findings:
        htitles[f.kind].append(f.title)
    for kind in sorted((set(btitles) | set(htitles)) - seen):
        b, h = sorted(btitles.get(kind, [])), sorted(htitles.get(kind, []))
        if b == h:
            for t in h:
                rows.append(HealthRow(kind=kind, title=t, direction="same"))
            continue
        for t in h:
            if t not in b:
                rows.append(HealthRow(kind=kind, title=t, after=t, direction="new"))
        for t in b:
            if t not in h:
                rows.append(HealthRow(kind=kind, title=t, before=t, direction="gone"))
        for t in h:
            if t in b:
                rows.append(HealthRow(kind=kind, title=t, direction="same"))
    order = {"worse": 0, "new": 1, "better": 2, "gone": 3, "same": 4}
    rows.sort(key=lambda r: order[r.direction])
    return rows
