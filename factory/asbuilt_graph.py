"""The as-built, computed: a repository's structure joined from what was read.

The reader reports what each file contains -- in any language, with no parser
per language -- and nothing it reports is shown as structure until this module
has checked it against the repository. An import names a file that exists and
whose name appears in the importer's text. A route called matches a route
served. A function's line holds its name. What cannot be checked is kept and
marked unconfirmed, never dropped and never promoted.

Everything here is deterministic. Subsystems are clusters of the confirmed
graph; parts are clusters of one big file's call graph; capabilities are ways in
and what they reach. A model names those groups afterwards and never forms them.
The same repository read twice gives the same groups, and a refresh starts from
the last reading's groups so that a part moves only when the code does.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from typing import Iterable, Literal

from pydantic import BaseModel, Field

from .schemas import ArchitectureAccount, CapabilityGroup, CapabilityMap, FileRecord, GroupNaming, SystemAccount

#: Where the as-built lives in the repository it describes.
AS_BUILT_DIR = ".fabrika/as-built"
#: The version of `AsBuilt` this code writes. A reading written by a newer
#: version is read as absent rather than half-understood.
AS_BUILT_VERSION = 1

#: A file this long is shown in parts, when its call graph separates. Below it,
#: the part and the file are the same thing and nothing changes.
PART_MIN_LINES = 1500
#: A part smaller than this is folded into its neighbour: a map of one-function
#: parts is a function list with extra steps.
PART_MIN_FUNCTIONS = 3
#: A file imported by at least this share of the code files -- and by at least
#: `HUB_MIN_IMPORTERS` -- is a hub. Left in the graph, a hub glues every cluster
#: into one, so it is set aside and shown as shared.
HUB_SHARE = 0.35
HUB_MIN_IMPORTERS = 5

#: Below this many lines of code, one file being a third of it is not news.
CONCENTRATION_MIN_LINES = 3000

EdgeKind = Literal["import", "launch", "route"]


# --------------------------------------------------------------------------
# the shape of a reading
# --------------------------------------------------------------------------


class Edge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    confirmed: bool = True
    note: str = ""


class FunctionNode(BaseModel):
    name: str
    line: int = 0
    end: int = 0
    confirmed: bool = False
    part: str = ""


class Part(BaseModel):
    key: str = Field(description="The part's largest function: what keeps it the same part across readings.")
    file: str
    name: str = ""
    summary: str = ""
    functions: list[str] = Field(default_factory=list)
    lines: int = 0
    calls_out: int = 0
    calls_in: int = 0


class PartLink(BaseModel):
    """Calls from one part of a file to another."""

    file: str
    source: str
    target: str
    calls: int = 0


class FileNode(BaseModel):
    path: str
    blob: str = ""
    lines: int = 0
    purpose: str = ""
    is_test: bool = False
    kind: str = "code"
    subsystem: str = ""
    placed_by: Literal["graph", "link", "hub", "test", "aside", "none"] = "none"
    generated: bool = Field(default=False, description=(
        "Made by a tool, as its header or its name says. Read and drawn like any file -- it is part of what "
        "runs -- but never put in front of a person to review: what to review is what generates it."))
    defines: list[str] = Field(default_factory=list)
    functions: list[FunctionNode] = Field(default_factory=list)
    parts: list[str] = Field(default_factory=list)
    entities_defined: list[str] = Field(default_factory=list)
    entities_read: list[str] = Field(default_factory=list)
    entities_written: list[str] = Field(default_factory=list)
    outside: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)
    ways_in: list[str] = Field(default_factory=list)
    tested_by: list[str] = Field(default_factory=list)


class Subsystem(BaseModel):
    key: str = Field(description="The subsystem's largest file: what keeps it the same subsystem across readings.")
    name: str = ""
    summary: str = ""
    files: list[str] = Field(default_factory=list)
    lines: int = 0
    code: str = Field(default="", description="Three capital letters that name it everywhere -- stable across readings.")
    role: Literal["product", "support", ""] = Field(default="", description=(
        "product: something a person does reaches it -- a capability runs code there, or a way in is "
        "declared in it. support: nothing does -- build configuration, dependencies, the environment, "
        "a bootstrap. Empty when the reading found no ways in to tell them apart."))


class WayInNode(BaseModel):
    id: str
    kind: str
    name: str
    method: str = ""
    file: str
    handler: str = ""
    handler_confirmed: bool = False
    callers: list[str] = Field(default_factory=list)
    unconfirmed_callers: list[str] = Field(default_factory=list)
    tested_by: list[str] = Field(default_factory=list)


class ReachItem(BaseModel):
    unit: str = Field(description="`part:<file>#<key>` or `file:<path>`.")
    file: str
    part: str = ""
    subsystem: str = ""
    lines: int = 0


class Capability(BaseModel):
    key: str
    name: str
    code: str = Field(default="", description="Three capital letters that name it everywhere -- stable across readings.")
    summary: str = ""
    area: str = ""
    ways_in: list[str] = Field(default_factory=list)
    direct: list[ReachItem] = Field(default_factory=list)
    in_motion: list[ReachItem] = Field(default_factory=list)
    tested: int = 0


class Finding(BaseModel):
    kind: str
    title: str
    detail: str
    paths: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    files_listed: int = 0
    files_read: int = 0
    skipped: list[dict[str, str]] = Field(default_factory=list)
    unread: list[dict[str, str]] = Field(default_factory=list)
    placed_graph: int = 0
    placed_link: int = 0
    hubs: int = 0
    tests: int = 0
    aside: int = 0
    unplaced: list[str] = Field(default_factory=list)
    edges_confirmed: int = 0
    edges_unconfirmed: int = 0
    functions_confirmed: int = 0
    functions_unconfirmed: int = 0
    unmatched_calls: list[dict[str, str]] = Field(default_factory=list)
    unresolved_links: list[dict[str, str]] = Field(default_factory=list)


class Runtime(BaseModel):
    """A process the product runs as, and the subsystems whose code runs in it."""

    key: str
    name: str
    what: str = ""
    kind: str = "other"
    subsystems: list[str] = Field(default_factory=list)
    ways_in: dict[str, int] = Field(default_factory=dict, description="Ways in declared here, by kind; exports left out.")


class ActorNode(BaseModel):
    name: str
    what: str = ""
    capabilities: list[str] = Field(default_factory=list, description="Capability keys.")
    enters: list[str] = Field(default_factory=list, description="Runtime keys where their capabilities start: "
                              "ways in nothing in the repository calls.")


class OutsideSystem(BaseModel):
    key: str
    name: str
    what: str = ""
    kind: str = "other"
    mentions: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list, description="Files that name it.")
    evidence: Literal["code", "named"] = Field(default="code", description=(
        "code: a file of code names it. named: only configuration or documentation does."))
    subsystems: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list, description="Capabilities that reach a file naming it.")
    calls_in: list[str] = Field(default_factory=list, description="Routes it calls that nothing here does.")
    tooling: bool = Field(default=False, description="It builds, tests or ships the system and is not used while "
                          "it runs: drawn with build and tooling, not beside what runs.")
    entities_written: list[str] = Field(default_factory=list)
    entities_read: list[str] = Field(default_factory=list)


class RuntimeLink(BaseModel):
    source: str
    target: str
    kind: Literal["http", "starts"] = "http"
    count: int = 0
    confirmed: bool = True


class Architecture(BaseModel):
    """How the system runs, drawn from the cartographer's account and checked
    against the graph: the placement is the model's, every arrow is counted."""

    runtimes: list[Runtime] = Field(default_factory=list)
    tooling: list[str] = Field(default_factory=list, description="Subsystems that build, configure or set up.")
    actors: list[ActorNode] = Field(default_factory=list)
    outside: list[OutsideSystem] = Field(default_factory=list)
    links: list[RuntimeLink] = Field(default_factory=list)
    dismissed: dict[str, list[str]] = Field(default_factory=dict, description="Mentions that are not outside "
                                            "systems, by why.")
    unsorted: list[str] = Field(default_factory=list, description="Mentions the account left out.")


class AsBuilt(BaseModel):
    """One reading of a repository at one commit."""

    version: int = AS_BUILT_VERSION
    commit: str = ""
    built_at: str = ""
    summary: str = ""
    start_reading: list[dict[str, str]] = Field(default_factory=list)
    files: list[FileNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    hubs: list[str] = Field(default_factory=list)
    subsystems: list[Subsystem] = Field(default_factory=list)
    parts: list[Part] = Field(default_factory=list)
    part_links: list[PartLink] = Field(default_factory=list)
    ways_in: list[WayInNode] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    not_capabilities: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    architecture: Architecture | None = None
    architecture_account: ArchitectureAccount | None = Field(default=None, description=(
        "The cartographer's answer the architecture was drawn from, kept so an unchanged system is not asked again."))
    coverage: Coverage = Field(default_factory=Coverage)
    cost: dict = Field(default_factory=dict)

    def file(self, path: str) -> FileNode | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    def subsystem_of(self, path: str) -> Subsystem | None:
        node = self.file(path)
        if node is None or not node.subsystem:
            return None
        for s in self.subsystems:
            if s.key == node.subsystem:
                return s
        return None


class Source(BaseModel):
    """A file as it is at the commit being read."""

    path: str
    blob: str = ""
    text: str = ""

    @property
    def lines(self) -> int:
        return self.text.count("\n") + (0 if self.text.endswith("\n") or not self.text else 1)


# --------------------------------------------------------------------------
# checking what the reader said
# --------------------------------------------------------------------------


def _norm_path(given: str, origin: str) -> str:
    given = (given or "").strip().strip("`'\"").replace("\\", "/")
    if not given:
        return ""
    if given.startswith("./") or given.startswith("../"):
        given = posixpath.join(posixpath.dirname(origin), given)
    return posixpath.normpath(given).lstrip("/")


def resolve_link(given: str, origin: str, paths: set[str], by_tail: dict[str, list[str]]) -> str:
    """The file a reported link names, or "" when it names none.

    Exact first. Then the one file whose path ends with what was given -- a
    reader that wrote `store.py` for `factory/store.py` meant the only
    `store.py` there is. Two candidates is not an answer, so it is none.
    """
    norm = _norm_path(given, origin)
    if not norm:
        return ""
    if norm in paths:
        return norm
    tail = norm.rsplit("/", 1)[-1]
    matches = [p for p in by_tail.get(tail, []) if p == norm or p.endswith("/" + norm)]
    if len(matches) == 1:
        return matches[0]
    stem_matches = [p for p in paths if posixpath.splitext(p)[0] == norm or posixpath.splitext(p)[0].endswith("/" + norm)]
    if len(stem_matches) == 1:
        return stem_matches[0]
    # A module named the way the language writes it -- `app.config`,
    # `com.acme.billing.Invoice` -- is a path with dots for slashes; a package
    # is its directory's `__init__`, `index` or `mod` file.
    raw = (given or "").strip()
    module = norm
    if "/" not in raw and "." in raw.strip(".") and posixpath.splitext(raw)[1].lstrip(".") not in _EXTENSIONS:
        module = raw.strip(".").replace(".", "/")
        found = [p for p in paths if posixpath.splitext(p)[0] == module or posixpath.splitext(p)[0].endswith("/" + module)]
        if len(found) == 1:
            return found[0]
    found = [p for p in paths if posixpath.splitext(posixpath.basename(p))[0] in ("__init__", "index", "mod")
             and (posixpath.dirname(p) == module or posixpath.dirname(p).endswith("/" + module))]
    return found[0] if len(found) == 1 else ""


_EXTENSIONS = {"py", "js", "jsx", "ts", "tsx", "mjs", "cjs", "rb", "go", "rs", "java", "kt", "ex", "exs", "cs",
               "php", "swift", "dart", "vue", "svelte", "css", "scss", "json", "yml", "yaml", "toml", "sql", "sh", "md", "html"}


def link_is_evident(text: str, target: str) -> bool:
    """Whether the importer's own text names the file it is said to link to.

    Language-neutral on purpose: a module name, a relative path, a script path
    -- every way of linking to a file writes some part of its name.
    """
    stem = posixpath.splitext(posixpath.basename(target))[0]
    if stem in ("__init__", "index", "mod", "main"):
        stem = posixpath.basename(posixpath.dirname(target)) or stem
    return bool(stem) and stem in text


_PARAM = re.compile(r"\$\{[^}]*\}|\{\{[^}]*\}\}|\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*|\[[^\]]*\]")


def norm_route(path: str) -> str:
    """A route template with every variable part written the same way."""
    p = (path or "").strip().strip("`'\"")
    p = re.sub(r"^[a-z]+://[^/]+", "", p)
    p = p.split("?", 1)[0].split("#", 1)[0]
    p = _PARAM.sub("{}", p)
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def match_route(called: str, method: str, served: dict[str, list[tuple[str, str, str]]]) -> tuple[str, str] | None:
    """The (file, way-in id) a call lands on, or None.

    `served` maps a normalised template to the (method, file, id) that serve
    it. An exact template wins. Failing that, a served route the call ends
    with -- a router mounted under a prefix the reader could not see -- when
    exactly one does.
    """
    n = norm_route(called)
    method = (method or "").upper()

    def pick(cands: list[tuple[str, str, str]]) -> tuple[str, str] | None:
        if method:
            same = [c for c in cands if not c[0] or c[0] == method]
            cands = same or cands
        files = {(c[1], c[2]) for c in cands}
        return sorted(files)[0] if files else None

    if n in served:
        return pick(served[n])
    tails = [t for t in served if t != "/" and (n.endswith(t) or t.endswith(n)) and len(t) > 1]
    if len(tails) == 1:
        return pick(served[tails[0]])
    return None


def confirm_functions(record: FileRecord, source: Source) -> list[FunctionNode]:
    """Each reported function at the line that holds its name, with its span.

    A reported line is taken when that line contains the name; otherwise the
    nearest line that does, within a short window. A name found nowhere near is
    kept unconfirmed and takes no part in the parts or the reach.
    """
    rows = source.text.splitlines()
    out: list[FunctionNode] = []
    seen: set[tuple[str, int]] = set()
    for fn in record.functions:
        name = (fn.name or "").strip().split(".")[-1].split("(")[0]
        if not name:
            continue
        line = fn.line if 0 < fn.line <= len(rows) else 0
        found = 0
        if line and name in rows[line - 1]:
            found = line
        else:
            centre = line or 1
            for delta in range(0, 40):
                for cand in (centre - delta, centre + delta):
                    if 0 < cand <= len(rows) and name in rows[cand - 1]:
                        found = cand
                        break
                if found:
                    break
        key = (name, found)
        if key in seen:
            continue
        seen.add(key)
        out.append(FunctionNode(name=name, line=found or fn.line, confirmed=bool(found)))
    confirmed = sorted((f for f in out if f.confirmed), key=lambda f: f.line)
    for i, f in enumerate(confirmed):
        nxt = confirmed[i + 1].line - 1 if i + 1 < len(confirmed) else len(rows)
        f.end = max(f.line, nxt)
    return out


# --------------------------------------------------------------------------
# clustering -- Louvain, deterministic, seedable
# --------------------------------------------------------------------------


def louvain(nodes: Iterable[str], weights: dict[tuple[str, str], float],
            seed: dict[str, str] | None = None, resolution: float = 1.0) -> dict[str, str]:
    """Communities of an undirected weighted graph, as node -> community label.

    Deterministic: nodes are visited in sorted order and ties keep the node
    where it is. `seed` is the starting partition -- the last reading's groups
    -- so a refresh moves a node only when its edges now pull it elsewhere.
    Labels in the result are the smallest member of each community, which is
    stable for as long as the membership is.
    """
    nodes = sorted(set(nodes))
    if not nodes:
        return {}
    adj: dict[str, dict[str, float]] = {n: {} for n in nodes}
    loop: dict[str, float] = {n: 0.0 for n in nodes}
    for (a, b), w in weights.items():
        if a not in adj or b not in adj or w <= 0:
            continue
        if a == b:
            loop[a] += w
        else:
            adj[a][b] = adj[a].get(b, 0.0) + w
            adj[b][a] = adj[b].get(a, 0.0) + w
    members: dict[str, list[str]] = {n: [n] for n in nodes}
    first = True
    while True:
        k = {n: sum(adj[n].values()) + 2 * loop[n] for n in adj}
        m2 = sum(k.values())
        if m2 <= 0:
            break
        if first and seed:
            comm = {n: (seed.get(n) or n) for n in adj}
        else:
            comm = {n: n for n in adj}
        first = False
        tot: dict[str, float] = defaultdict(float)
        for n in adj:
            tot[comm[n]] += k[n]
        moved_any = False
        for _ in range(50):
            moved = False
            for n in sorted(adj):
                cn = comm[n]
                tot[cn] -= k[n]
                kin: dict[str, float] = defaultdict(float)
                for v, w in adj[n].items():
                    kin[comm[v]] += w
                best, best_gain = cn, kin.get(cn, 0.0) - resolution * tot[cn] * k[n] / m2
                for c in sorted(kin):
                    gain = kin[c] - resolution * tot[c] * k[n] / m2
                    if gain > best_gain + 1e-12:
                        best, best_gain = c, gain
                tot[best] += k[n]
                if best != cn:
                    comm[n] = best
                    moved = True
                    moved_any = True
            if not moved:
                break
        labels = sorted(set(comm.values()))
        if len(labels) == len(adj) and not moved_any:
            break
        new_adj: dict[str, dict[str, float]] = {c: {} for c in labels}
        new_loop: dict[str, float] = {c: 0.0 for c in labels}
        new_members: dict[str, list[str]] = {c: [] for c in labels}
        for n in adj:
            c = comm[n]
            new_members[c].extend(members[n])
            new_loop[c] += loop[n]
            for v, w in adj[n].items():
                d = comm[v]
                if c == d:
                    new_loop[c] += w / 2
                else:
                    new_adj[c][d] = new_adj[c].get(d, 0.0) + w
        if len(labels) == len(adj):
            members = new_members
            break
        adj, loop, members = new_adj, new_loop, new_members
    out: dict[str, str] = {}
    for group in members.values():
        label = min(group)
        for n in group:
            out[n] = label
    for n in nodes:
        out.setdefault(n, n)
    return out


def _groups(assign: dict[str, str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for node, label in assign.items():
        groups[label].append(node)
    return {k: sorted(v) for k, v in groups.items()}


# --------------------------------------------------------------------------
# the join
# --------------------------------------------------------------------------


def way_in_id(kind: str, name: str, method: str = "") -> str:
    return f"{kind}:{(method + ' ') if method else ''}{name}".strip()


def build(sources: dict[str, Source], records: dict[str, FileRecord], *,
          commit: str = "", built_at: str = "", previous: AsBuilt | None = None,
          skipped: list[dict[str, str]] | None = None,
          unread: list[dict[str, str]] | None = None) -> AsBuilt:
    """Join the records into a checked graph, grouped and traced.

    Names and capability groups are left empty; `apply_names` and
    `apply_capabilities` fill them from the cartographer's answers.
    """
    paths = set(records)
    by_tail: dict[str, list[str]] = defaultdict(list)
    for p in sorted(sources):
        by_tail[p.rsplit("/", 1)[-1]].append(p)
    all_paths = set(sources)
    cov = Coverage(files_listed=len(sources) + len(skipped or []), files_read=len(records),
                   skipped=list(skipped or []), unread=list(unread or []))

    nodes: dict[str, FileNode] = {}
    for path in sorted(records):
        rec, src = records[path], sources[path]
        kind = "test" if rec.is_test else rec.kind
        nodes[path] = FileNode(
            path=path, blob=src.blob, lines=src.lines, purpose=rec.purpose.strip(),
            is_test=kind == "test", kind=kind, defines=sorted(set(rec.defines)),
            functions=confirm_functions(rec, src),
            entities_defined=_uniq(rec.entities_defined), entities_read=_uniq(rec.entities_read),
            entities_written=_uniq(rec.entities_written), outside=_uniq(rec.outside),
            domain_terms=_uniq(rec.domain_terms), generated=is_generated(path, src.text))

    # -- file links --------------------------------------------------------
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def add(src: str, dst: str, kind: EdgeKind, confirmed: bool, note: str = "") -> None:
        key = (src, dst, kind)
        if src == dst or key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(Edge(source=src, target=dst, kind=kind, confirmed=confirmed, note=note))

    for path in sorted(records):
        rec, text = records[path], sources[path].text
        for kind, links in (("import", rec.imports), ("launch", rec.launches)):
            for given in links:
                target = resolve_link(given, path, all_paths, by_tail)
                if not target:
                    cov.unresolved_links.append({"file": path, "kind": kind, "named": given})
                    continue
                if target not in paths:
                    continue
                confirmed = kind == "import" and link_is_evident(text, target)
                note = "" if confirmed else ("started by path, not imported" if kind == "launch"
                                            else "the importer's text does not name it")
                add(path, target, kind, confirmed, note)

    # -- ways in, and routes called ----------------------------------------
    ways: dict[str, WayInNode] = {}
    served: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for path in sorted(records):
        names = {f.name for f in nodes[path].functions if f.confirmed}
        for w in records[path].ways_in:
            if not w.name.strip():
                continue
            wid = way_in_id(w.kind, w.name.strip(), (w.method or "").upper() if w.kind == "route" else "")
            if wid in ways:
                continue
            handler = (w.handler or "").strip().split(".")[-1]
            ways[wid] = WayInNode(id=wid, kind=w.kind, name=w.name.strip(), method=(w.method or "").upper(),
                                  file=path, handler=handler, handler_confirmed=handler in names)
            nodes[path].ways_in.append(wid)
            if w.kind == "route":
                served[norm_route(w.name)].append(((w.method or "").upper(), path, wid))
    for path in sorted(records):
        # A note, a report or a data file that names a route is not calling
        # it: only what runs -- code, and the configuration that wires it --
        # is a caller. Measured: a testing report and a verification script
        # under `work/` were listed as what calls a capability.
        if nodes[path].kind not in ("code", "config", "test") or _PROSE.search(path):
            continue
        for call in records[path].routes_called:
            hit = match_route(call.path, call.method, served)
            if hit is None:
                cov.unmatched_calls.append({"file": path, "path": call.path, "method": call.method})
                continue
            target, wid = hit
            way = ways[wid]
            bucket = way.callers
            if path not in bucket:
                bucket.append(path)
            if target != path:
                add(path, target, "route", True)

    # -- hubs, tests, subsystems -------------------------------------------
    # Only code is drawn. Documents, mockups and data are read and listed, and
    # configuration joins only when something links to it or it links out --
    # a docs folder that mentions every file must not glue the map together.
    linked_any = {e.source for e in edges} | {e.target for e in edges}
    drawn = [p for p in sorted(nodes)
             if nodes[p].kind == "code" or (nodes[p].kind == "config" and p in linked_any)]
    code = [p for p in drawn if not nodes[p].is_test]
    importers: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.kind == "import" and e.source in drawn:
            importers[e.target].add(e.source)
    importing = {e.source for e in edges if e.kind == "import" and e.source in code}
    hub_floor = max(HUB_MIN_IMPORTERS, HUB_SHARE * len(importing))
    hubs = sorted(p for p in code if len(importers[p]) >= hub_floor)
    for p in nodes:
        if nodes[p].is_test:
            nodes[p].placed_by = "test"
        elif p in hubs:
            nodes[p].placed_by = "hub"
        elif p not in drawn:
            nodes[p].placed_by = "aside"

    member = [p for p in code if p not in hubs]
    weights: dict[tuple[str, str], float] = defaultdict(float)
    linked: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.source in member and e.target in member:
            w = 1.0 if e.confirmed else 0.5
            a, b = sorted((e.source, e.target))
            weights[(a, b)] += w
            linked[e.source].add(e.target)
            linked[e.target].add(e.source)
    seed = None
    if previous is not None:
        prev_of = {f.path: f.subsystem for f in previous.files if f.subsystem}
        seed = {p: prev_of[p] for p in member if p in prev_of}
    assign = louvain([p for p in member if linked[p]], dict(weights), seed=seed)
    groups = _groups(assign)
    # A group of one that is linked at all belongs with what it links to most.
    for label, files in sorted(groups.items()):
        if len(files) != 1:
            continue
        only = files[0]
        pulls: dict[str, float] = defaultdict(float)
        for (a, b), w in weights.items():
            other = b if a == only else a if b == only else None
            if other and assign.get(other) and assign[other] != label:
                pulls[assign[other]] += w
        if pulls:
            assign[only] = sorted(pulls.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    groups = _groups(assign)
    subsystems: list[Subsystem] = []
    for label, files in groups.items():
        anchor = sorted(files, key=lambda f: (-nodes[f].lines, f))[0]
        subsystems.append(Subsystem(key=anchor, files=files, lines=sum(nodes[f].lines for f in files)))
        for f in files:
            nodes[f].subsystem = anchor
            nodes[f].placed_by = "graph" if len(files) > 1 or linked[f] else "none"
    subsystems.sort(key=lambda s: (-s.lines, s.key))
    for s in subsystems:
        for f in s.files:
            if not any(e for e in edges if e.kind == "import" and f in (e.source, e.target)) and linked[f]:
                nodes[f].placed_by = "link"

    # -- parts of big files ------------------------------------------------
    parts: list[Part] = []
    prev_parts: dict[str, dict[str, str]] = defaultdict(dict)
    if previous is not None:
        for f in previous.files:
            for fn in f.functions:
                if fn.part:
                    prev_parts[f.path][fn.name] = fn.part
    calls = _call_index(nodes, records, edges)
    part_links: list[PartLink] = []
    for path in sorted(nodes):
        node = nodes[path]
        if node.lines < PART_MIN_LINES or node.kind != "code":
            continue
        mine = _split(node, calls, prev_parts.get(path))
        parts.extend(mine)
        if mine:
            part_links += [PartLink(file=path, source=a, target=b, calls=n)
                           for (a, b), n in sorted(part_crossings(node, calls).items())]

    # -- reach, tests ------------------------------------------------------
    for way in ways.values():
        way.tested_by = _tests_of_way(way, nodes, records, calls)
    for p, node in nodes.items():
        if node.is_test:
            continue
        testers = {e.source for e in edges if e.target == p and nodes[e.source].is_test}
        names = {f.name for f in node.functions if f.confirmed}
        for t, tnode in nodes.items():
            if tnode.is_test and any(c in names for fn in records[t].functions for c in fn.calls):
                if any(e for e in edges if e.source == t and e.target == p) or p in _imports_of(t, edges):
                    testers.add(t)
        node.tested_by = sorted(testers)

    cov.hubs = len(hubs)
    cov.tests = sum(1 for n in nodes.values() if n.is_test)
    cov.aside = sum(1 for n in nodes.values() if n.placed_by == "aside")
    cov.placed_graph = sum(1 for n in nodes.values() if n.placed_by == "graph")
    cov.placed_link = sum(1 for n in nodes.values() if n.placed_by == "link")
    cov.unplaced = sorted(p for p, n in nodes.items() if n.placed_by == "none")
    for p in cov.unplaced:
        nodes[p].subsystem = ""
    subsystems = [s for s in subsystems if len(s.files) > 1 or nodes[s.files[0]].placed_by != "none"]
    cov.edges_confirmed = sum(1 for e in edges if e.confirmed)
    cov.edges_unconfirmed = sum(1 for e in edges if not e.confirmed) + len(cov.unmatched_calls)
    cov.functions_confirmed = sum(1 for n in nodes.values() for f in n.functions if f.confirmed)
    cov.functions_unconfirmed = sum(1 for n in nodes.values() for f in n.functions if not f.confirmed)

    reading = AsBuilt(commit=commit, built_at=built_at, files=[nodes[p] for p in sorted(nodes)],
                      edges=sorted(edges, key=lambda e: (e.source, e.target, e.kind)), hubs=hubs,
                      subsystems=subsystems, parts=parts, part_links=part_links,
                      ways_in=[ways[k] for k in sorted(ways)],
                      coverage=cov)
    if previous is not None:
        carry_names(reading, previous)
    reading.findings = findings(reading)
    return reading


def _uniq(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for x in items:
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    return out


def _imports_of(path: str, edges: list[Edge]) -> set[str]:
    return {e.target for e in edges if e.source == path and e.kind in ("import", "launch")}


# --------------------------------------------------------------------------
# the call graph, and parts
# --------------------------------------------------------------------------


FnId = tuple[str, str, int]  # (file, name, line)


def _call_index(nodes: dict[str, FileNode], records: dict[str, FileRecord],
                edges: list[Edge]) -> dict[FnId, set[FnId]]:
    """Which confirmed function calls which, across the repository.

    A called name resolves to a function of that name in the same file first,
    then in a file this one imports. A name that resolves to neither is the
    standard library or a package, and is not an edge.
    """
    by_name: dict[str, dict[str, list[FunctionNode]]] = {}
    for p, n in nodes.items():
        idx: dict[str, list[FunctionNode]] = defaultdict(list)
        for f in n.functions:
            if f.confirmed:
                idx[f.name].append(f)
        by_name[p] = idx
    imported: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.kind == "import":
            imported[e.source].append(e.target)
    out: dict[FnId, set[FnId]] = defaultdict(set)
    for p, rec in records.items():
        confirmed = {(f.name, f.line) for f in nodes[p].functions if f.confirmed}
        for fn in rec.functions:
            name = (fn.name or "").strip().split(".")[-1].split("(")[0]
            mine = [f for f in by_name[p].get(name, []) if (f.name, f.line) in confirmed]
            if not mine:
                continue
            here = min(mine, key=lambda f: abs(f.line - fn.line)) if fn.line else mine[0]
            src: FnId = (p, here.name, here.line)
            for called in fn.calls:
                c = (called or "").strip().split(".")[-1].split("(")[0]
                if not c or c == name:
                    continue
                targets = by_name[p].get(c) or []
                where = p
                if not targets:
                    for other in imported.get(p, []):
                        if by_name.get(other, {}).get(c):
                            targets, where = by_name[other][c], other
                            break
                for t in targets:
                    out[src].add((where, t.name, t.line))
    return out


def _split(node: FileNode, calls: dict[FnId, set[FnId]], prev: dict[str, str] | None) -> list[Part]:
    """A big file's parts: clusters of its own call graph, or none."""
    fns = {(node.path, f.name, f.line): f for f in node.functions if f.confirmed}
    if len(fns) < 2 * PART_MIN_FUNCTIONS:
        return []
    ids = {fid: f"{fid[1]}@{fid[2]}" for fid in fns}
    weights: dict[tuple[str, str], float] = defaultdict(float)
    for src, targets in calls.items():
        if src not in fns:
            continue
        for t in targets:
            if t in fns and t != src:
                a, b = sorted((ids[src], ids[t]))
                weights[(a, b)] += 1.0
    linked = {n for pair in weights for n in pair}
    seed = None
    if prev:
        seed = {ids[fid]: prev[f.name] for fid, f in fns.items() if f.name in prev}
    assign = louvain(sorted(linked), dict(weights), seed=seed)
    groups = [g for g in _groups(assign).values()]
    size = {ids[fid]: max(1, f.end - f.line + 1) for fid, f in fns.items()}
    # fold parts too small to be worth a name into the neighbour they call most
    big = [g for g in groups if len(g) >= PART_MIN_FUNCTIONS]
    if len(big) < 2:
        return []
    home = {n: i for i, g in enumerate(big) for n in g}
    for g in groups:
        if len(g) >= PART_MIN_FUNCTIONS:
            continue
        for n in g:
            pulls: dict[int, float] = defaultdict(float)
            for (a, b), w in weights.items():
                other = b if a == n else a if b == n else None
                if other in home:
                    pulls[home[other]] += w
            if pulls:
                home[n] = sorted(pulls.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    members: dict[int, list[str]] = defaultdict(list)
    for n, i in home.items():
        members[i].append(n)
    by_id = {v: k for k, v in ids.items()}
    parts: list[Part] = []
    for i, names in sorted(members.items()):
        names.sort(key=lambda n: (-size[n], n))
        key = by_id[names[0]][1]
        part = Part(key=key, file=node.path, functions=[by_id[n][1] for n in names],
                    lines=sum(size[n] for n in names))
        for n in names:
            fns[by_id[n]].part = key
        parts.append(part)
    crossing = part_crossings(node, calls)
    for p in parts:
        p.calls_out = sum(n for (a, b), n in crossing.items() if a == p.key)
        p.calls_in = sum(n for (a, b), n in crossing.items() if b == p.key)
    parts.sort(key=lambda p: (-p.lines, p.key))
    node.parts = [p.key for p in parts]
    return parts


def part_crossings(node: FileNode, calls: dict[FnId, set[FnId]]) -> dict[tuple[str, str], int]:
    """Calls from one part of a file to another, counted per direction."""
    part_of = {(node.path, f.name, f.line): f.part for f in node.functions if f.confirmed and f.part}
    out: dict[tuple[str, str], int] = defaultdict(int)
    for src, targets in calls.items():
        a = part_of.get(src)
        if not a:
            continue
        for t in targets:
            b = part_of.get(t)
            if b and b != a:
                out[(a, b)] += 1
    return dict(out)


# --------------------------------------------------------------------------
# reach: what a way in does, and what it sets in motion
# --------------------------------------------------------------------------


def _unit(nodes: dict[str, FileNode], fid: FnId) -> tuple[str, str, str]:
    path, name, line = fid
    node = nodes.get(path)
    part = ""
    if node is not None and node.parts:
        for f in node.functions:
            if f.name == name and f.line == line:
                part = f.part
                break
    return (f"part:{path}#{part}" if part else f"file:{path}", path, part)


def _fn_lines(nodes: dict[str, FileNode], fid: FnId) -> int:
    node = nodes.get(fid[0])
    if node is None:
        return 0
    for f in node.functions:
        if f.name == fid[1] and f.line == fid[2]:
            return max(1, f.end - f.line + 1)
    return 0


def _follow(way: WayInNode, nodes: dict[str, FileNode],
            calls: dict[FnId, set[FnId]]) -> tuple[set[FnId], set[FnId]] | None:
    """The functions a way in runs -- its handler and what that calls -- and
    those one call further. None when it has no confirmed handler to start from."""
    handler = [(way.file, f.name, f.line) for f in nodes[way.file].functions
               if f.confirmed and f.name == way.handler] if way.handler_confirmed and way.file in nodes else []
    if not handler:
        return None
    first = set(handler)
    for h in handler:
        first |= calls.get(h, set())
    second: set[FnId] = set()
    for f in first:
        second |= calls.get(f, set())
    return first, second - first


def reach(reading: AsBuilt, records: dict[str, FileRecord], way_ids: Iterable[str]) -> tuple[list[ReachItem], list[ReachItem]]:
    """What these ways in run directly, and what that sets in motion.

    Following every call to its end is useless in a pipeline, where approving
    one thing starts a run that touches everything. So there are two depths:
    the handler and what it calls (`direct`), and one call further on
    (`in_motion`). A way in with no confirmed handler falls back to its file
    and the files it imports.
    """
    nodes = {f.path: f for f in reading.files}
    calls = _call_index(nodes, records, reading.edges)
    ways = {w.id: w for w in reading.ways_in}
    direct_fns: set[FnId] = set()
    motion_fns: set[FnId] = set()
    direct_files: set[str] = set()
    motion_files: set[str] = set()
    for wid in way_ids:
        way = ways.get(wid)
        if way is None:
            continue
        depths = _follow(way, nodes, calls)
        if depths is None:
            direct_files.add(way.file)
            motion_files |= _imports_of(way.file, reading.edges)
            continue
        direct_fns |= depths[0]
        motion_fns |= depths[1]
    motion_fns -= direct_fns
    motion_files -= direct_files

    def roll(fns: set[FnId], files: set[str]) -> list[ReachItem]:
        acc: dict[str, ReachItem] = {}
        for fid in fns:
            unit, path, part = _unit(nodes, fid)
            item = acc.setdefault(unit, ReachItem(unit=unit, file=path, part=part,
                                                  subsystem=nodes[path].subsystem if path in nodes else ""))
            item.lines += _fn_lines(nodes, fid)
        for path in files:
            if path in nodes:
                unit = f"file:{path}"
                acc.setdefault(unit, ReachItem(unit=unit, file=path, subsystem=nodes[path].subsystem,
                                               lines=nodes[path].lines))
        return sorted(acc.values(), key=lambda r: (-r.lines, r.unit))

    direct = roll(direct_fns, direct_files)
    have = {r.unit for r in direct}
    motion = [r for r in roll(motion_fns, motion_files) if r.unit not in have]
    return direct, motion


def _tests_of_way(way: WayInNode, nodes: dict[str, FileNode], records: dict[str, FileRecord],
                  calls: dict[FnId, set[FnId]]) -> list[str]:
    """Test files that exercise a way in: by calling its route, or its handler."""
    out: set[str] = set()
    for p, node in nodes.items():
        if not node.is_test:
            continue
        rec = records[p]
        if way.kind == "route" and any(norm_route(c.path) == norm_route(way.name) for c in rec.routes_called):
            out.add(p)
            continue
        if way.handler and any(way.handler in fn.calls for fn in rec.functions):
            out.add(p)
    return sorted(out)


# --------------------------------------------------------------------------
# one file's source, with what the reading knows about its lines
# --------------------------------------------------------------------------


_LINKISH = re.compile(r"\b(import|from|require|include|use|using|source|load|extends|mod)\b|<script|<link|@import|src=|href=")


def _link_line(lines: list[str], target: str) -> int:
    """The line that names a file this one links to: the first that looks like
    a link and writes the target's name, else the first that writes it at all.
    The same evidence the link was confirmed on, pointed at."""
    stem = posixpath.splitext(posixpath.basename(target))[0]
    if stem in ("__init__", "index", "mod", "main"):
        stem = posixpath.basename(posixpath.dirname(target)) or stem
    if not stem:
        return 0
    word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])")
    plain = 0
    for i, text in enumerate(lines, 1):
        if word.search(text):
            if _LINKISH.search(text):
                return i
            plain = plain or i
    return plain


def _route_line(lines: list[str], path: str) -> int:
    """The line a route is called from: the one that writes the most of the
    route's fixed text, its longest fixed piece first -- and, between equals,
    the one whose literal ends where the route does, so `/api/cards` is not
    found on the line that builds `/api/cards/{id}`."""
    pieces = [x for x in re.split(r"\$\{[^}]*\}|\{[^}]*\}|<[^>]*>|:[A-Za-z_]\w*", path) if x.strip("/")]
    if not pieces:
        return 0
    longest = max(pieces, key=len)
    ends = re.compile(re.escape(pieces[-1]) + r"""['"`]""") if not re.search(r"[{<:]", path.rstrip("/").rsplit("/", 1)[-1]) else None
    best, score = 0, 0.0
    for i, text in enumerate(lines, 1):
        if longest not in text:
            continue
        n = sum(1 for x in pieces if x in text) + (0.5 if ends and ends.search(text) else 0)
        if n > score:
            best, score = i, n
    return best


def _span_end(lines: list[str], line: int, fallback: int) -> int:
    """Where a function that starts at `line` ends, by its indentation: before
    the next line written no deeper than its own start. A closing brace at that
    depth is its last line; a closing parenthesis there only ends its signature.
    The reading's own span runs to the next function, which is right for
    counting lines and wrong for one defined inside another."""
    if not 0 < line <= len(lines):
        return fallback
    head = lines[line - 1]
    depth = len(head) - len(head.lstrip())
    end = line
    for i in range(line, len(lines)):
        text = lines[i]
        body = text.strip()
        if not body:
            continue
        if len(text) - len(text.lstrip()) > depth or body[0] in ")]":
            end = i + 1
            continue
        if body[0] == "}" or body in ("end", "fi", "done", "esac"):
            end = i + 1
        break
    return end


def source_notes(reading: AsBuilt, records: dict[str, FileRecord], path: str, text: str) -> dict:
    """What the reading knows about each line of one file, for reading it.

    Only what code confirmed is pinned to a line: a function at the line that
    holds its name, a link at the line that writes its target's name, a route
    call at the line that writes the route. Each carries where it leads --
    the function a call resolves to, the file imported, the handler a call
    reaches -- and a function carries which capabilities run it and who else
    calls it, which is what someone answering for the file needs next to it.
    """
    nodes = {f.path: f for f in reading.files}
    node = nodes.get(path)
    if node is None:
        return {"functions": [], "ways_in": [], "links": []}
    lines = text.splitlines()
    calls = _call_index(nodes, records, reading.edges)
    called_by: dict[FnId, set[FnId]] = defaultdict(set)
    for src, targets in calls.items():
        for t in targets:
            if t[0] == path and t != src:
                called_by[t].add(src)
    ways = {w.id: w for w in reading.ways_in}
    runs: dict[FnId, dict[str, str]] = defaultdict(dict)
    for cap in reading.capabilities:
        for wid in cap.ways_in:
            way = ways.get(wid)
            depths = _follow(way, nodes, calls) if way else None
            if depths is None:
                continue
            for depth, fns in zip(("direct", "in_motion"), depths):
                for fid in fns:
                    if fid[0] == path and runs[fid].get(cap.key) != "direct":
                        runs[fid][cap.key] = depth

    def where(fid: FnId) -> dict:
        other = nodes.get(fid[0])
        return {"file": fid[0], "name": fid[1], "line": fid[2],
                "test": bool(other and other.is_test), "subsystem": other.subsystem if other else ""}

    functions = []
    for f in node.functions:
        fid: FnId = (path, f.name, f.line)
        functions.append({
            "name": f.name, "line": f.line if f.confirmed else 0,
            "end": _span_end(lines, f.line, f.end) if f.confirmed else 0,
            "part": f.part, "confirmed": f.confirmed,
            "calls": [where(t) for t in sorted(calls.get(fid, set()))],
            "called_by": [where(t) for t in sorted(called_by.get(fid, set()))],
            "capabilities": [{"key": k, "depth": d} for k, d in sorted(runs.get(fid, {}).items())],
        })
    handler_line = {f.name: f.line for f in node.functions if f.confirmed}
    ways_in = [{"id": w.id, "line": handler_line.get(w.handler, 0) if w.handler_confirmed else 0}
               for w in reading.ways_in if w.file == path]

    served = {}
    for w in reading.ways_in:
        if w.kind == "route":
            served.setdefault(norm_route(w.name), []).append(w)
    links = []
    for e in reading.edges:
        if e.source != path or e.kind not in ("import", "launch"):
            continue
        links.append({"kind": e.kind, "target": e.target, "confirmed": e.confirmed,
                      "line": _link_line(lines, e.target)})
    record = records.get(path)
    for call in (record.routes_called if record else []):
        hits = [w for w in served.get(norm_route(call.path), [])
                if not call.method or not w.method or call.method.upper() == w.method.upper()]
        links.append({"kind": "route", "target": hits[0].file if hits else "", "confirmed": bool(hits),
                      "route": f"{call.method.upper()} {call.path}".strip(), "way_in": hits[0].id if hits else "",
                      "line": _route_line(lines, call.path)})
    links.sort(key=lambda x: (x["line"] or 10**9, x["kind"], x["target"]))
    return {"functions": functions, "ways_in": ways_in, "links": links}


# --------------------------------------------------------------------------
# what the cartographer supplies
# --------------------------------------------------------------------------


def carry_names(reading: AsBuilt, previous: AsBuilt) -> None:
    """Keep the last reading's names for groups that are still the same group.

    Same key and mostly the same members is the same group; renaming it would
    make a person relearn a map that did not change. Anything else is left
    unnamed for the cartographer.
    """
    prev_subs = {s.key: s for s in previous.subsystems}
    for s in reading.subsystems:
        old = prev_subs.get(s.key)
        if old and old.name and _overlap(s.files, old.files) >= 0.5:
            s.name, s.summary, s.code = old.name, old.summary, old.code
    prev_parts = {(p.file, p.key): p for p in previous.parts}
    for p in reading.parts:
        old = prev_parts.get((p.file, p.key))
        if old and old.name and _overlap(p.functions, old.functions) >= 0.5:
            p.name, p.summary = old.name, old.summary
    if not reading.summary and previous.summary:
        reading.summary = previous.summary
        reading.start_reading = [r for r in previous.start_reading if reading.file(r.get("path", ""))]


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, len(sa | sb))


def apply_names(reading: AsBuilt, naming: GroupNaming, *, what: Literal["subsystem", "part"],
                file: str = "") -> None:
    """Names for groups, by key. A key the cartographer did not answer keeps a
    plain fallback name, so nothing on the map is ever blank."""
    got = {g.key: g for g in naming.groups}
    if what == "subsystem":
        for s in reading.subsystems:
            g = got.get(s.key)
            if g:
                s.name, s.summary = g.name.strip(), g.summary.strip()
                s.code = s.code or (g.code or "").strip().upper()
            if not s.name:
                s.name = posixpath.dirname(s.key) or s.key
    else:
        for p in reading.parts:
            if p.file != file:
                continue
            g = got.get(p.key)
            if g:
                p.name, p.summary = g.name.strip(), g.summary.strip()
            if not p.name:
                p.name = f"around {p.key}"


def apply_account(reading: AsBuilt, account: SystemAccount) -> None:
    reading.summary = account.summary.strip()
    reading.start_reading = [{"path": s.path, "why": s.why.strip()} for s in account.start_reading
                             if reading.file(s.path) and not reading.file(s.path).generated]


def carry_capabilities(reading: AsBuilt, previous: AsBuilt) -> CapabilityMap | None:
    """The last reading's grouping, when the ways in are exactly the same ones.

    A different set is a different question and goes back to the cartographer.
    """
    if not previous.capabilities or {w.id for w in reading.ways_in} != {w.id for w in previous.ways_in}:
        return None
    return CapabilityMap(
        capabilities=[CapabilityGroup(name=c.name, code=c.code, summary=c.summary, area=c.area, ways_in=list(c.ways_in))
                      for c in previous.capabilities if c.key != "not-yet-grouped"],
        not_capabilities=list(previous.not_capabilities))


def apply_capabilities(reading: AsBuilt, records: dict[str, FileRecord], capmap: CapabilityMap) -> None:
    """Capabilities from the cartographer's grouping -- every way in exactly once.

    The grouping is the model's; the membership rule is not. A way in placed
    twice stays where it was placed first. A way in placed nowhere is not lost:
    it goes into `Not yet grouped`, which a person can see. (INV-4, one level up)
    """
    known = {w.id for w in reading.ways_in}
    placed: set[str] = set()
    caps: list[Capability] = []
    plumbing = [w for w in capmap.not_capabilities if w in known]
    placed |= set(plumbing)
    for g in capmap.capabilities:
        mine = [w for w in g.ways_in if w in known and w not in placed]
        if not mine:
            continue
        placed |= set(mine)
        caps.append(Capability(key=_slug(g.name), name=g.name.strip(), code=(g.code or "").strip().upper(),
                               summary=g.summary.strip(), area=g.area.strip(), ways_in=mine))
    rest = sorted(known - placed)
    if rest:
        caps.append(Capability(key="not-yet-grouped", name="Not yet grouped",
                               summary="Ways in the grouping left out. Shown so nothing is hidden.",
                               ways_in=rest))
    ways = {w.id: w for w in reading.ways_in}
    seen_keys: set[str] = set()
    for c in caps:
        base, n = c.key, 2
        while c.key in seen_keys:
            c.key = f"{base}-{n}"
            n += 1
        seen_keys.add(c.key)
        c.direct, c.in_motion = reach(reading, records, c.ways_in)
        c.tested = sum(1 for w in c.ways_in if ways[w].tested_by)
    reading.capabilities = caps
    reading.not_capabilities = sorted(plumbing)
    classify_subsystems(reading)
    reading.findings = findings(reading)


_CODE = re.compile(r"^[A-Z]{3}$")
_QUIET = {"a", "an", "and", "the", "of", "for", "to", "in", "on", "with", "its", "it", "by", "at", "or", "from"}


def derive_code(name: str, taken: set[str]) -> str:
    """Three letters from a name: the initials of its words that carry meaning,
    filled from the last of them, then varied until nothing else has it."""
    words = [w for w in re.findall(r"[A-Za-z]+", (name or "").replace("'", "").replace("’", ""))]
    strong = [w for w in words if w.lower() not in _QUIET] or words or ["X"]
    base = "".join(w[0] for w in strong[:3])
    tail = strong[-1][1:] if strong else ""
    for ch in tail:
        if len(base) >= 3:
            break
        base += ch
    base = (base + "XXX")[:3].upper()
    free = lambda c: c not in taken  # noqa: E731
    if free(base):
        return base
    letters = "".join(strong).upper()
    for third in dict.fromkeys(letters[2:] + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        cand = base[:2] + third
        if free(cand):
            return cand
    for second in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for third in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            cand = base[0] + second + third
            if free(cand):
                return cand
    return base


def assign_codes(reading: AsBuilt, previous: AsBuilt | None = None) -> None:
    """A three-letter ID for every subsystem and capability, unique across both.

    A number was a position, and moved whenever the order did; this names the
    group, so it can be said in a finding, a packet or a conversation. What the
    cartographer proposed is kept when it is three capital letters nobody else
    has; the last reading's code for the same group is kept before that, so an
    ID moves only when the group does; and a code a dissolved group held is not
    handed to a different one. Anything else is derived from the name.
    """
    prev_owner: dict[str, str] = {}
    if previous is not None:
        prev_owner.update({s.code: f"sub:{s.key}" for s in previous.subsystems if s.code})
        prev_owner.update({c.code: f"cap:{c.key}" for c in previous.capabilities if c.code})
    groups = [(f"sub:{s.key}", s) for s in reading.subsystems] + [(f"cap:{c.key}", c) for c in reading.capabilities]
    taken: set[str] = set()
    # Carried codes first: a group that kept its code keeps it.
    order = sorted(groups, key=lambda g: 0 if prev_owner.get(g[1].code) == g[0] else 1)

    def usable(owner: str, group: Subsystem | Capability) -> str:
        code = (group.code or "").strip().upper()
        held_by_other = code in prev_owner and prev_owner[code] != owner
        return "" if not _CODE.match(code) or held_by_other else code

    # Every code a group may keep is held for it before any is derived, so a
    # replacement never takes the code the model gave another group.
    asked = {owner: usable(owner, group) for owner, group in groups}
    for owner, group in order:
        code = asked[owner]
        if not code or code in taken:
            held = {c for o, c in asked.items() if c and o != owner}
            code = derive_code(group.name, taken | held | {c for c, o in prev_owner.items() if o != owner})
        group.code = code
        taken.add(code)


def labelled(name: str, code: str) -> str:
    return f"{name} ({code})" if code else name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "group"


def classify_subsystems(reading: AsBuilt) -> None:
    """Product or support, computed from what a person can do -- and the
    product ones first, so they carry the low numbers everywhere they appear.

    Product: a capability's handlers run code there or set work in motion
    there, or one of a capability's ways in is declared in it. Support: nothing a person does
    reaches it. In one project four of seven subsystems are build
    configuration, dependency lists, the development environment and the front
    end's bootstrap, and drawn at the same weight as the three that do the
    work they read as a third of the system each. A reading with no ways in at all
    cannot tell the two apart, and says nothing.
    """
    if not reading.ways_in:
        for s in reading.subsystems:
            s.role = ""
        return
    reached = {r.subsystem for c in reading.capabilities for r in c.direct + c.in_motion if r.subsystem}
    nodes = {f.path: f for f in reading.files}
    # Only a way in that is part of a capability. The app's root screen, a
    # health check, a version route are plumbing -- otherwise a front end's
    # bootstrap counts as product for declaring `/` and nothing else.
    counted = {w for c in reading.capabilities for w in c.ways_in}
    declared = {nodes[w.file].subsystem for w in reading.ways_in
                if w.id in counted and w.file in nodes and nodes[w.file].subsystem}
    for s in reading.subsystems:
        s.role = "product" if s.key in reached or s.key in declared else "support"
    reading.subsystems.sort(key=lambda s: (s.role != "product", -s.lines, s.key))


# --------------------------------------------------------------------------
# how it runs
# --------------------------------------------------------------------------


_SETTINGS = re.compile(r"^(config|configuration|settings|conf|env|environment|constants)$")


#: Files that build, test, package or ship a repository rather than run in it:
#: container and compose files, CI, task runners, dependency lists, and the
#: settings of the tools that build and check it.
_SETUP_NAME = re.compile(
    r"^(dockerfile|containerfile|makefile|gnumakefile|justfile|jenkinsfile|vagrantfile|tiltfile|earthfile"
    r"|.+\.dockerfile|dockerfile\..+|(docker-)?compose(\..+)?\.ya?ml"
    r"|\.gitlab-ci\.yml|\.travis\.yml|azure-pipelines\.ya?ml|bitbucket-pipelines\.yml|cloudbuild\.ya?ml"
    r"|buildspec\.ya?ml|\.pre-commit-config\.yaml|tox\.ini|noxfile\.py"
    r"|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|pyproject\.toml|setup\.(py|cfg)|requirements.*\.txt"
    r"|pipfile(\.lock)?|poetry\.lock|uv\.lock|go\.(mod|sum)|cargo\.(toml|lock)|gemfile(\.lock)?"
    r"|composer\.(json|lock)|pom\.xml|(build|settings)\.gradle(\.kts)?"
    r"|tsconfig.*\.json|.+\.config\.[cm]?[jt]s|\.eslintrc.*|\.prettierrc.*)$")
_SETUP_DIR = re.compile(r"^(\.github|\.circleci|\.buildkite|\.devcontainer|\.fabrika)/")


def _sets_up(path: str) -> bool:
    """A file that builds, tests or ships the repository. A system only these
    files name is not one it uses while it runs, whatever it was called."""
    return bool(_SETUP_DIR.match(path) or _SETUP_NAME.match(posixpath.basename(path).lower()))


def _configures(path: str) -> bool:
    """A file that only sets things up: naming a system there is configuring
    it, not using it."""
    name = posixpath.basename(path)
    return name.startswith(".env") or bool(_SETTINGS.match(posixpath.splitext(name)[0].lower()))


_GENERATED_MARK = re.compile(
    r"auto[- ]?generated|@generated|\bgenerated (?:by|from|using|with|code|file|automatically)\b|\bcode generated\b"
    r"|do not (?:edit|modify)(?: (?:this file|by hand|manually)|\s*[.!:\-\u2013\u2014]|\s*$)", re.I)
_COMMENT = re.compile(r"^\s*(//|#|/\*|\*|--|<!--|;|%|\"\"\"|\'\'\')")
_GENERATED_NAME = re.compile(
    r"(\.g|\.freezed|\.gr|\.pb|_pb2|_pb2_grpc|\.generated|_generated)\.[A-Za-z0-9]+$|(^|/)(__generated__|generated)/")


def is_generated(path: str, text: str) -> bool:
    """Whether a tool made this file: a comment in its first lines says so, or
    its name is a generator's -- `.g.dart`, `_pb2.py`, a `generated/`
    directory. A file that only imports something generated, or prose that
    says "do not modify the original", says nothing of the kind."""
    if _GENERATED_NAME.search(path):
        return True
    head = [ln for ln in text.splitlines()[:8] if ln.strip()][:5]
    return any(_COMMENT.match(ln) and _GENERATED_MARK.search(ln) for ln in head)


#: Written for people, whatever the reader called it: a report that names a
#: route is not calling it, even when it is a report on tests.
_PROSE = re.compile(r"\.(md|mdx|markdown|txt|rst|adoc)$", re.I)


def mentions_of(reading: AsBuilt) -> dict[str, list[str]]:
    """Every outside system a file named, with the files that named it."""
    out: dict[str, list[str]] = defaultdict(list)
    for f in reading.files:
        for m in f.outside:
            if m.strip() and f.path not in out[m.strip()]:
                out[m.strip()].append(f.path)
    return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())))


def uncalled_routes(reading: AsBuilt) -> list[WayInNode]:
    """Routes this repository serves and nothing in it calls -- the ones an
    outside system may be calling."""
    return [w for w in reading.ways_in if w.kind == "route" and not w.callers and not w.unconfirmed_callers]


def architecture_prompt(reading: AsBuilt) -> str:
    files = {f.path: f for f in reading.files}
    subs = "\n\n".join(
        f"## key: {s.key}\n{s.code} {s.name} -- {len(s.files)} files, {s.lines:,} lines. {s.summary}\n"
        + "\n".join(f"- {p}: {files[p].purpose[:140]}" for p in s.files[:12])
        + (f"\n- ... and {len(s.files) - 12} more" if len(s.files) > 12 else "")
        for s in reading.subsystems)
    caps = "\n".join(f"- {c.code}: {c.name} -- {c.summary}" for c in reading.capabilities)
    mentions = "\n".join(f"- {m} -- named by {len(ps)} file{'s' if len(ps) != 1 else ''}: {', '.join(ps[:3])}"
                         for m, ps in mentions_of(reading).items())
    routes = "\n".join(f"- {w.id} -- in {w.file}" + (f", handled by {w.handler}" if w.handler else "")
                       for w in uncalled_routes(reading))
    return ("# Task\n\nSay how this system runs. Place every subsystem in the process its code runs in, or in "
            "tooling if nothing of it runs in production. Name the people who use it, by role, with the "
            "capabilities each uses. Sort every mention below into the outside system it names -- one it "
            "talks to while it runs, or one that builds, tests or ships it -- or dismiss it and say why. Copy keys, codes, mentions and ids exactly.\n\n"
            f"# Subsystems\n\n{subs}\n\n# Capabilities\n\n{caps or '(none)'}\n\n"
            f"# Mentions of things outside the repository\n\n{mentions or '(none)'}\n\n"
            f"# Routes nothing in this repository calls\n\n{routes or '(none)'}\n")


def carry_architecture(reading: AsBuilt, previous: AsBuilt | None) -> ArchitectureAccount | None:
    """The last answer, when the question is the same one: the same subsystems,
    no mention it did not sort, no uncalled route it was not shown."""
    if previous is None or previous.architecture_account is None:
        return None
    acct = previous.architecture_account
    if {s.key for s in reading.subsystems} != {s.key for s in previous.subsystems}:
        return None
    sorted_before = {m for o in acct.outside for m in o.mentions} | {d.mention for d in acct.dismissed}
    if not set(mentions_of(reading)) <= sorted_before | set(previous.architecture.unsorted if previous.architecture else []):
        return None
    if not {w.id for w in uncalled_routes(reading)} <= {w.id for w in uncalled_routes(previous)}:
        return None
    return acct


def apply_architecture(reading: AsBuilt, account: ArchitectureAccount) -> None:
    """How it runs, from the cartographer's account, held to the graph.

    The model places and names; code keeps it honest. A subsystem goes in one
    place, first wins, and one it left out is placed by its role rather than
    lost. A mention is one system's or dismissed, and one it did not sort is
    listed. A route an outside system is said to call must be one nothing here
    calls. Every arrow and count comes from the graph: HTTP between processes
    from the confirmed route calls, a system's files from who named it, a
    person's way in from the capabilities whose entry points nobody here calls.
    """
    nodes = {f.path: f for f in reading.files}
    subs = {s.key: s for s in reading.subsystems}
    by_code = {c.code: c for c in reading.capabilities if c.code}
    caps = {c.key: c for c in reading.capabilities}
    placed: dict[str, str] = {}
    runtimes: list[Runtime] = []
    for g in account.runtimes:
        mine = [k for k in dict.fromkeys(g.subsystems) if k in subs and k not in placed]
        if not g.name.strip():
            continue
        key = _slug(g.name)
        while any(r.key == key for r in runtimes):
            key += "-2"
        runtimes.append(Runtime(key=key, name=g.name.strip(), what=g.what.strip(), kind=g.kind, subsystems=mine))
        placed.update({k: key for k in mine})
    tooling = [k for k in dict.fromkeys(account.tooling) if k in subs and k not in placed]
    for s in reading.subsystems:
        if s.key in placed or s.key in tooling:
            continue
        if s.role == "support":
            tooling.append(s.key)
            continue
        if not any(r.key == "not-placed" for r in runtimes):
            runtimes.append(Runtime(key="not-placed", name="Not placed", what="the account did not say where these run"))
        next(r for r in runtimes if r.key == "not-placed").subsystems.append(s.key)
        placed[s.key] = "not-placed"
    runtimes = [r for r in runtimes if r.subsystems]
    run_of = lambda path: placed.get(nodes[path].subsystem, "") if path in nodes else ""  # noqa: E731
    for r in runtimes:
        kinds: dict[str, int] = defaultdict(int)
        for w in reading.ways_in:
            if w.kind != "export" and run_of(w.file) == r.key:
                kinds[w.kind] += 1
        r.ways_in = dict(sorted(kinds.items()))

    # what an outside system is, and what code says about it
    mentions = mentions_of(reading)
    folded = {m.lower(): m for m in mentions}
    exact = lambda m: m if m in mentions else folded.get((m or "").strip().lower(), "")  # noqa: E731
    uncalled = {w.id for w in uncalled_routes(reading)}
    taken: set[str] = set()
    claimed: set[str] = set()
    outside: list[OutsideSystem] = []
    for g in account.outside:
        mine = []
        for m in g.mentions:
            m = exact(m)
            if m and m not in taken:
                mine.append(m)
                taken.add(m)
        calls = [w for w in dict.fromkeys(g.calls_in) if w in uncalled and w not in claimed]
        claimed |= set(calls)
        if not g.name.strip() or not (mine or calls):
            continue
        files = sorted({p for m in mine for p in mentions[m]})
        code_files = [p for p in files if nodes[p].kind == "code" and not nodes[p].is_test and not _configures(p)]
        # Tooling is the model's word or the files', and code has the last
        # one. Named only where the repository is built, tested or shipped, it
        # is tooling whatever the model called it; named by code that runs --
        # a settings module included, since configuring a system is using it
        # -- it is not, whatever the model called it. Which subsystem a
        # Dockerfile was grouped with, or a health check a container probes,
        # says nothing either way.
        runs_with = [p for p in files if nodes[p].kind == "code" and not nodes[p].is_test and not _sets_up(p)]
        setup_only = bool(files) and all(_sets_up(p) or nodes[p].is_test for p in files)
        builds_it = not runs_with and (g.kind == "tooling" or setup_only)
        kind = "tooling" if builds_it else ("other" if g.kind == "tooling" else g.kind)
        subs_of = sorted({nodes[p].subsystem for p in files if nodes[p].subsystem})
        key = _slug(g.name)
        while any(o.key == key for o in outside):
            key += "-2"
        reach_files = set(files) | {w.file for w in reading.ways_in if w.id in calls}
        outside.append(OutsideSystem(
            key=key, name=g.name.strip(), what=g.what.strip(), kind=kind, mentions=mine, files=files,
            evidence="code" if code_files else "named", subsystems=subs_of, tooling=builds_it,
            runtimes=[] if builds_it else [
                r.key for r in runtimes if any(placed.get(s) == r.key for s in subs_of)
                or any(run_of(w.file) == r.key for w in reading.ways_in if w.id in calls)],
            capabilities=[c.key for c in reading.capabilities
                          if any(ri.file in reach_files for ri in c.direct + c.in_motion)],
            calls_in=calls,
            entities_written=sorted({e for p in code_files for e in nodes[p].entities_written}) if kind == "datastore" else [],
            entities_read=sorted({e for p in code_files for e in nodes[p].entities_read}) if kind == "datastore" else []))
    dismissed: dict[str, list[str]] = defaultdict(list)
    for d in account.dismissed:
        m = exact(d.mention)
        if m and m not in taken:
            dismissed[d.why].append(m)
            taken.add(m)
    unsorted = [m for m in mentions if m not in taken]

    # who uses it, and where they come in
    actors: list[ActorNode] = []
    for a in account.actors:
        keys = list(dict.fromkeys((by_code.get(x.strip().upper()) or caps.get(x.strip()) or Capability(key="", name="")).key
                                  for x in a.capabilities))
        keys = [k for k in keys if k]
        if not a.name.strip():
            continue
        ways = {w for k in keys for w in caps[k].ways_in}
        entries = [w for w in reading.ways_in if w.id in ways and w.kind != "export" and w.id not in claimed
                   and not w.callers and not w.unconfirmed_callers]
        # A person comes in through a screen or a command; a route counts only
        # where they have neither -- an API is then what they use.
        personal = [w for w in entries if w.kind in ("screen", "command")] or entries
        enters = [r.key for r in runtimes if any(run_of(w.file) == r.key for w in personal)]
        actors.append(ActorNode(name=a.name.strip(), what=a.what.strip(), capabilities=keys, enters=enters))

    # between processes: HTTP from the route calls code matched, and one
    # process starting another
    counts: dict[tuple[str, str, str, bool], int] = defaultdict(int)
    for e in reading.edges:
        if e.kind not in ("route", "launch"):
            continue
        a, b = run_of(e.source), run_of(e.target)
        if a and b and a != b:
            counts[(a, b, "http" if e.kind == "route" else "starts", e.confirmed)] += 1
    links = [RuntimeLink(source=a, target=b, kind=k, count=n, confirmed=c)
             for (a, b, k, c), n in sorted(counts.items())]
    reading.architecture = Architecture(runtimes=runtimes, tooling=tooling, actors=actors, outside=outside,
                                        links=links, dismissed=dict(dismissed), unsorted=unsorted)
    reading.architecture_account = account


# --------------------------------------------------------------------------
# findings -- what a person underwriting this should know, computed
# --------------------------------------------------------------------------


def findings(reading: AsBuilt) -> list[Finding]:
    out: list[Finding] = []
    nodes = {f.path: f for f in reading.files}
    # What a person should look at is what a person wrote: a generated file is
    # in the graph, and in no finding.
    code = [f for f in reading.files if f.kind == "code" and not f.is_test and not f.generated]
    total = sum(f.lines for f in code) or 1
    name_of = {s.key: labelled(s.name or s.key, s.code) for s in reading.subsystems}

    big = sorted(code, key=lambda f: -f.lines)
    for k in (1, 2, 3) if total >= CONCENTRATION_MIN_LINES else ():
        top = big[:k]
        share = sum(f.lines for f in top) / total
        if len(top) == k and share >= 0.3 and all(f.lines / total >= 0.1 for f in top):
            names = ", ".join(f"{f.path} ({f.lines:,} lines)" for f in top)
            out.append(Finding(kind="concentration",
                               title=f"{_count(k, 'file')} {'is' if k == 1 else 'are'} {round(share * 100)}% of the code",
                               detail=f"{names}, of {total:,} lines outside the tests.",
                               paths=[f.path for f in top]))
            break

    pair: dict[tuple[str, str], int] = defaultdict(int)
    for e in reading.edges:
        a, b = nodes[e.source].subsystem, nodes[e.target].subsystem
        if a and b and a != b:
            pair[(a, b)] += 1
    for (a, b), n in sorted(pair.items()):
        if a < b and (b, a) in pair:
            out.append(Finding(kind="mutual",
                               title=f"{name_of.get(a, a)} and {name_of.get(b, b)} depend on each other",
                               detail=f"{n} link{'s' if n != 1 else ''} one way, {pair[(b, a)]} the other.",
                               paths=[a, b]))

    launched = sorted({e.target for e in reading.edges if e.kind == "launch"}
                      - {e.target for e in reading.edges if e.kind == "import"})
    if launched:
        out.append(Finding(kind="launched",
                           title=f"{_count(len(launched), 'file')} {'is' if len(launched) == 1 else 'are'} started, never imported",
                           detail="Run as a separate process or loaded by path: " + ", ".join(launched)
                           + ". The link is reported by the reader and not confirmed by code.",
                           paths=launched))

    if any(w.callers for w in reading.ways_in):
        plumbing = set(reading.not_capabilities)
        orphan = [w for w in reading.ways_in if w.kind == "route" and not w.callers and not w.tested_by
                  and w.id not in plumbing and not nodes[w.file].generated]
        if orphan:
            out.append(Finding(kind="no_caller",
                               title=f"{_count(len(orphan), 'route')} {'has' if len(orphan) == 1 else 'have'} no caller in this repository",
                               detail=", ".join(f"{w.method} {w.name}".strip() for w in orphan[:8])
                               + (" and more" if len(orphan) > 8 else "")
                               + ". Either unused, or called from somewhere this reading did not see.",
                               paths=sorted({w.file for w in orphan})))

    if reading.ways_in:
        starts = {w.file for w in reading.ways_in}
        reached = set(starts)
        frontier = list(starts)
        while frontier:
            p = frontier.pop()
            for e in reading.edges:
                if e.source == p and e.target not in reached:
                    reached.add(e.target)
                    frontier.append(e.target)
        clients = {e.source for e in reading.edges if e.kind == "route"}
        unreached = [f.path for f in code if f.path not in reached and f.path not in reading.hubs
                     and f.path not in clients and not any(e.target == f.path for e in reading.edges)]
        if unreached:
            out.append(Finding(kind="unreached",
                               title=f"{_count(len(unreached), 'file')} no way in reaches",
                               detail="Nothing imports or starts them, and they declare no way in: "
                               + ", ".join(unreached[:8]) + (" and more" if len(unreached) > 8 else "")
                               + ". Dead, or loaded by a convention this reading cannot see.",
                               paths=unreached))

    tests = [f for f in reading.files if f.is_test]
    if tests:
        untested = [f for f in code if not f.tested_by and f.lines >= 50]
        if untested:
            share = sum(f.lines for f in untested) / total
            out.append(Finding(kind="untested",
                               title=f"{round(share * 100)}% of the code is not reached by a test",
                               detail=f"{_count(len(untested), 'file')} of 50 lines or more that no test imports or calls; "
                               "largest: " + ", ".join(f.path for f in sorted(untested, key=lambda f: -f.lines)[:4]) + ".",
                               paths=[f.path for f in untested]))
    else:
        out.append(Finding(kind="untested", title="There are no tests",
                           detail="No file in this repository reads as a test.", paths=[]))

    by_file: dict[str, list[Part]] = defaultdict(list)
    for p in reading.parts:
        by_file[p.file].append(p)
    for path, parts in sorted(by_file.items()):
        inside = sum(p.lines for p in parts) or 1
        cand = [p for p in parts if p.lines >= 0.08 * inside]
        if not cand:
            continue
        best = min(cand, key=lambda p: ((p.calls_in + p.calls_out) / max(1, p.lines), p.key))
        out.append(Finding(kind="seams",
                           title=f"{path} comes apart into {len(parts)} parts",
                           detail=f"The cleanest first cut is {best.name or best.key} -- {best.lines:,} lines, "
                           f"{best.calls_out} calls out and {best.calls_in} in.",
                           paths=[path]))

    unconfirmed = reading.coverage.edges_unconfirmed
    if unconfirmed:
        out.append(Finding(kind="unconfirmed",
                           title=f"{_count(unconfirmed, 'link')} could not be confirmed",
                           detail=f"{len(reading.coverage.unmatched_calls)} calls to the back end matched no route, "
                           f"and {unconfirmed - len(reading.coverage.unmatched_calls)} links were reported but "
                           "not found in the code's own text. They are drawn dashed.",
                           paths=[]))
    return out


def _count(n: int, noun: str) -> str:
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    return f"{words.get(n, str(n))} {noun}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------
# freshness, and what a change did to the system
# --------------------------------------------------------------------------


class SystemChange(BaseModel):
    """What a feature changed in the system, not just in files. Computed."""

    base: str = ""
    head: str = ""
    files_added: list[str] = Field(default_factory=list)
    files_removed: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    unplaced_added: list[str] = Field(default_factory=list)
    dependencies_added: list[dict[str, str]] = Field(default_factory=list)
    dependencies_removed: list[dict[str, str]] = Field(default_factory=list)
    ways_in_added: list[str] = Field(default_factory=list)
    ways_in_removed: list[str] = Field(default_factory=list)
    subsystems_added: list[str] = Field(default_factory=list)
    subsystems_removed: list[str] = Field(default_factory=list)
    parts_changed: list[dict[str, str]] = Field(default_factory=list)
    outside_added: list[str] = Field(default_factory=list)
    entities_added: list[str] = Field(default_factory=list)

    def empty(self) -> bool:
        return not any(getattr(self, k) for k in self.model_fields if k not in ("base", "head"))

    def lines(self) -> list[str]:
        """The change in sentences, for the packet."""
        out: list[str] = []
        if self.files_added or self.files_removed or self.files_changed:
            bits = []
            if self.files_added:
                placed = len(self.files_added) - len(self.unplaced_added)
                bits.append(f"{_count(len(self.files_added), 'new file').lower()}, "
                            + ("all placed" if not self.unplaced_added else f"{placed} placed"))
            if self.files_changed:
                bits.append(f"{_count(len(self.files_changed), 'file').lower()} changed")
            if self.files_removed:
                bits.append(f"{_count(len(self.files_removed), 'file').lower()} removed")
            out.append("; ".join(bits).capitalize() + ".")
        for d in self.dependencies_added:
            out.append(f"Adds a dependency from {d['from']} to {d['to']}.")
        for d in self.dependencies_removed:
            out.append(f"Removes the dependency from {d['from']} to {d['to']}.")
        if self.ways_in_added:
            out.append(f"Adds {_count(len(self.ways_in_added), 'way in').lower()}: " + ", ".join(self.ways_in_added[:6]) + ".")
        if self.ways_in_removed:
            out.append(f"Removes {_count(len(self.ways_in_removed), 'way in').lower()}: " + ", ".join(self.ways_in_removed[:6]) + ".")
        for s in self.subsystems_added:
            out.append(f"A new subsystem forms around {s}.")
        for s in self.subsystems_removed:
            out.append(f"The subsystem around {s} is gone.")
        for p in self.parts_changed:
            out.append(f"{p['file']}: {p['change']}.")
        if self.outside_added:
            out.append("Talks to something new outside the repository: " + ", ".join(self.outside_added) + ".")
        if self.entities_added:
            out.append("New data entities: " + ", ".join(self.entities_added) + ".")
        return out


def system_change(base: AsBuilt, head: AsBuilt) -> SystemChange:
    """The difference between two readings, in the system's own terms."""
    bf = {f.path: f for f in base.files}
    hf = {f.path: f for f in head.files}
    change = SystemChange(base=base.commit, head=head.commit)
    change.files_added = sorted(set(hf) - set(bf))
    change.files_removed = sorted(set(bf) - set(hf))
    change.files_changed = sorted(p for p in set(bf) & set(hf) if bf[p].blob and bf[p].blob != hf[p].blob)
    change.unplaced_added = [p for p in change.files_added if hf[p].placed_by == "none"]

    def sub_name(reading: AsBuilt, key: str) -> str:
        for s in reading.subsystems:
            if s.key == key:
                return labelled(s.name or key, s.code)
        return key

    def deps(reading: AsBuilt) -> set[tuple[str, str]]:
        files = {f.path: f for f in reading.files}
        out = set()
        for e in reading.edges:
            a, b = files[e.source].subsystem, files[e.target].subsystem
            if a and b and a != b:
                out.add((a, b))
        return out

    bd, hd = deps(base), deps(head)
    change.dependencies_added = [{"from": sub_name(head, a), "to": sub_name(head, b)} for a, b in sorted(hd - bd)]
    change.dependencies_removed = [{"from": sub_name(base, a), "to": sub_name(base, b)} for a, b in sorted(bd - hd)]
    bw, hw = {w.id for w in base.ways_in}, {w.id for w in head.ways_in}
    change.ways_in_added = sorted(hw - bw)
    change.ways_in_removed = sorted(bw - hw)
    bs, hs = {s.key for s in base.subsystems}, {s.key for s in head.subsystems}
    change.subsystems_added = [sub_name(head, k) for k in sorted(hs - bs)]
    change.subsystems_removed = [sub_name(base, k) for k in sorted(bs - hs)]
    bp = defaultdict(set)
    hp = defaultdict(set)
    for p in base.parts:
        bp[p.file].add(p.key)
    for p in head.parts:
        hp[p.file].add(p.key)
    for path in sorted(set(bp) | set(hp)):
        if bp[path] == hp[path]:
            continue
        if not bp[path]:
            change.parts_changed.append({"file": path, "change": f"now large enough to show in {len(hp[path])} parts"})
        elif not hp[path]:
            change.parts_changed.append({"file": path, "change": "no longer shown in parts"})
        else:
            added = len(hp[path] - bp[path])
            removed = len(bp[path] - hp[path])
            change.parts_changed.append({"file": path, "change": f"{added} part{'s' if added != 1 else ''} formed, {removed} dissolved"})
    outside_b = {o for f in base.files for o in f.outside}
    outside_h = {o for f in head.files for o in f.outside}
    change.outside_added = sorted(outside_h - outside_b)
    ent_b = {e for f in base.files for e in f.entities_defined}
    ent_h = {e for f in head.files for e in f.entities_defined}
    change.entities_added = sorted(ent_h - ent_b)
    return change
