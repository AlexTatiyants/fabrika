"""A branch's diff, cut into the pieces a person reads it in.

A branch Fabrika did not build has no spec, so there is no criterion to reach
its code through. It is reached by materiality instead (DESIGN.md, The Code
View): the diff is cut into one piece per function, setting or test, and each
piece is classed novel, conventional, mechanical or generated -- tests are a
group of their own -- with the reason for its class.

The cut is code, here, and the only thing a model is asked is which class each
piece is and why. That split is the point. A view sorted by materiality puts
the pieces called mechanical out of sight, and if the thing that cut the pieces
could also drop a line, folding would be a way to hide one. So:

**Every Line Once.** Every line the branch added or removed is in exactly one
piece. Each side of a file is outlined into units that *partition* it -- every
line of the file belongs to one unit -- so a changed line has exactly one home
by construction, and `account` checks it anyway, after the cut and again after
any join a model asked for. A join that would break it is refused, not trusted.

Nothing here reads a working tree. Both sides come out of git by commit, so the
cut is the same whichever checkout asks.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable, Sequence

from . import git
from .schemas import Located, Piece, PieceJudgement

#: How much of an unchanged run is kept beside a change, and at a unit's top.
CONTEXT = 3
#: Rows drawn per piece. The lines are all accounted for past this; only the
#: drawing stops, and says so.
MAX_ROWS = 400
#: A changed run in a file no outline divides is one piece with the next one
#: when this few unchanged lines separate them.
RUN_GAP = 3

#: Files a tool writes. Classed by code: nobody chose their contents line by line.
LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "Cargo.lock", "go.sum",
    "composer.lock", "Gemfile.lock", "mix.lock", "pubspec.lock", "packages.lock.json",
}

UNCLASSED = "Not classified by the rapporteur. Unclassified defaults to novel."
UNREASONED = "The rapporteur gave no reason for its class, so it is read as novel."

#: Heaviest first. A join takes the heaviest class among what it joins, so
#: joining can never make code lighter than it was called piece by piece.
WEIGHT = ["novel", "conventional", "mechanical", "generated"]


class LineAccountError(RuntimeError):
    """A cut that lost a line or put one in two places."""


# ==========================================================================
# the diff, as git says
# ==========================================================================


@dataclass
class FileChange:
    path: str
    old_path: str
    status: str                     # A, M, D, R
    hunks: list[tuple[int, int, int, int]] = field(default_factory=list)
    binary: bool = False

    @property
    def added(self) -> set[int]:
        return {n for _, _, hs, hc in self.hunks for n in range(hs, hs + hc)}

    @property
    def removed(self) -> set[int]:
        return {n for bs, bc, _, _ in self.hunks for n in range(bs, bs + bc)}


_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def read_diff(repo, base: str, head: str) -> list[FileChange]:
    """Every file the branch touched between two commits, with its hunks."""
    done = git.run(["-c", "core.quotepath=off", "diff", "-U0", "--no-color", "--no-ext-diff",
                    "--find-renames", base, head], repo, timeout=180)
    if done is None or done.returncode != 0:
        raise LineAccountError(f"git could not diff {base[:12]}..{head[:12]}: "
                               f"{(done.stderr if done else 'git did not run')[:300]}")
    return parse_diff(done.stdout)


def parse_diff(text: str) -> list[FileChange]:
    out: list[FileChange] = []
    current: FileChange | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            current = FileChange(path=header.group(2), old_path=header.group(1), status="M")
            out.append(current)
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "A"
        elif line.startswith("deleted file mode"):
            current.status = "D"
        elif line.startswith("rename from "):
            current.old_path, current.status = line[len("rename from "):], "R"
        elif line.startswith("rename to "):
            current.path = line[len("rename to "):]
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.binary = True
        elif line.startswith("--- ") and not line.startswith("--- /dev/null"):
            current.old_path = line[6:] if line.startswith("--- a/") else current.old_path
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            current.path = line[6:] if line.startswith("+++ b/") else current.path
        else:
            hunk = _HUNK.match(line)
            if hunk:
                bs, bc = int(hunk.group(1)), int(hunk.group(2) if hunk.group(2) is not None else 1)
                hs, hc = int(hunk.group(3)), int(hunk.group(4) if hunk.group(4) is not None else 1)
                current.hunks.append((bs, bc, hs, hc))
    for change in out:
        if change.status == "D":
            change.path = change.old_path
    return out


def rows_of(change: FileChange, base: list[str], head: list[str]) -> list[list]:
    """The whole file as rows, unchanged and changed: [op, base no, head no, text]."""
    rows: list[list] = []
    b = h = 1
    for bs, bc, hs, hc in sorted(change.hunks):
        # `-n,0` means "inserted after base line n": the unchanged run reaches n.
        until = bs if bc else bs + 1
        while b < until and b <= len(base):
            rows.append([" ", b, h, base[b - 1]])
            b, h = b + 1, h + 1
        for _ in range(bc):
            rows.append(["-", b, 0, base[b - 1] if b <= len(base) else ""])
            b += 1
        for _ in range(hc):
            rows.append(["+", 0, h, head[h - 1] if h <= len(head) else ""])
            h += 1
    while b <= len(base):
        rows.append([" ", b, h, base[b - 1]])
        b, h = b + 1, h + 1
    return rows


# ==========================================================================
# outlines: every line of a file in exactly one unit
# ==========================================================================


@dataclass
class Unit:
    symbol: str
    kind: str
    start: int
    end: int


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    if any(part in ("tests", "test", "__tests__", "spec", "specs") for part in p.parts[:-1]):
        return True
    return bool(re.match(r"(test_.*\.py|.*_test\.(py|go|rb|exs?)|.*\.(test|spec)\.\w+)$", name))


def _blankish(line: str, comment: tuple[str, ...]) -> bool:
    s = line.strip()
    return not s or s.startswith(comment)


def _partition(anchors: list[tuple[int, int, str, str]], n: int, lines: list[str],
               comment: tuple[str, ...], top: str = "module top",
               lo: int = 1, hi: int | None = None) -> list[Unit]:
    """Units that cover lines lo..hi with no gap and no overlap.

    Each anchor (start, end, symbol, kind) owns from just above it -- the
    comments and blank lines leading into it -- down to where the next one
    begins. What comes before the first anchor is `top`.
    """
    hi = n if hi is None else hi
    if hi < lo:
        return []
    anchors = sorted(a for a in anchors if lo <= a[0] <= hi)
    if not anchors:
        return [Unit(top, "module", lo, hi)]
    starts: list[int] = []
    floor = lo
    for i, (start, _end, _s, _k) in enumerate(anchors):
        pulled = start
        while pulled - 1 >= floor and pulled - 1 >= lo and _blankish(lines[pulled - 2], comment):
            pulled -= 1
        starts.append(max(pulled, floor))
        floor = max(anchors[i][1], start) + 1
    units: list[Unit] = []
    if starts[0] > lo:
        units.append(Unit(top, "module", lo, starts[0] - 1))
    for i, (start, _end, symbol, kind) in enumerate(anchors):
        end = (starts[i + 1] - 1) if i + 1 < len(anchors) else hi
        units.append(Unit(symbol, kind, starts[i], max(end, starts[i])))
    return units


def _python(text: str, lines: list[str], test: bool) -> list[Unit] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    n = len(lines)

    def first(node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", []) or []
        return min([node.lineno] + [d.lineno for d in decorators])

    def fn_kind(name: str) -> str:
        return "test" if test and name.startswith("test") else "function"

    def end_of(node: ast.AST) -> int:
        return getattr(node, "end_lineno", None) or node.lineno

    # A router made with a prefix -- `router = APIRouter(prefix="/api/cards")`,
    # a Flask `Blueprint(..., url_prefix=...)` -- puts it in front of every
    # path its decorators name. Without it `@router.delete("/{card_id}")` read
    # as `DELETE /{card_id}`, which is no route anyone could call.
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if (kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            prefixes[target.id] = kw.value.value.rstrip("/")

    def route_of(node: ast.AST) -> str:
        """`PUT /api/x` for a handler registered by decorator: what a reader
        calls it, and what the as-built calls its way in."""
        for d in getattr(node, "decorator_list", []):
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in _VERBS
                    and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)):
                owner = d.func.value.id if isinstance(d.func.value, ast.Name) else ""
                path = prefixes.get(owner, "") + d.args[0].value
                return f"{d.func.attr.upper()} {path or '/'}"
        return ""

    def anchors_of(body: list[ast.stmt], owner: str, in_class: bool) -> list[tuple]:
        found: list[tuple] = []
        for node in body:
            start, end = first(node), end_of(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = route_of(node) or (f"{owner}.{node.name}" if owner else node.name)
                found.append((start, end, name, fn_kind(node.name), node))
            elif isinstance(node, ast.ClassDef):
                found.append((start, end, f"{owner}.{node.name}" if owner else node.name, "class", node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and (in_class or not owner):
                if in_class:
                    # A class's fields, one unit per run of them. Named for the
                    # run rather than its first field, so adding a field leaves
                    # the unit the same unit on both sides.
                    if found and found[-1][4] == "fields":
                        found[-1] = (found[-1][0], end, found[-1][2], "setting", "fields")
                    else:
                        found.append((node.lineno, end, f"{owner}, fields", "setting", "fields"))
                else:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    found.append((node.lineno, end, ", ".join(ast.unparse(t) for t in targets)[:80],
                                  "setting", None))
            elif owner:
                continue                # inside a body: stays with the unit it falls in
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # One unit for a run of imports, not one per line.
                if found and found[-1][2] == "imports" and found[-1][1] >= node.lineno - 2:
                    found[-1] = (found[-1][0], end, "imports", "module", None)
                else:
                    found.append((node.lineno, end, "imports", "module", None))
            elif node is body[0] and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue                # the module docstring: the top of the module
            else:
                found.append((node.lineno, end, "module code", "module", None))
        return found

    def divide(lo: int, hi: int, body: list[ast.stmt], owner: str, in_class: bool,
               top: str, top_kind: str, depth: int) -> list[Unit]:
        anchors = anchors_of(body, owner, in_class)
        units = _partition([a[:4] for a in anchors], n, lines, ("#",), top=top, lo=lo, hi=hi)
        out: list[Unit] = []
        for unit in units:
            if unit.symbol == top and unit.kind == "module":
                unit.kind = top_kind
            node = next((a[4] for a in anchors if a[2] == unit.symbol and unit.start <= a[0] <= unit.end), None)
            nested = ([s for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else [])
            if depth < 3 and isinstance(node, ast.ClassDef):
                # A class, divided method by method inside the lines it owns.
                out.extend(divide(unit.start, unit.end, node.body, unit.symbol, True,
                                  unit.symbol, "class", depth + 1))
            elif depth < 3 and len(nested) >= 2 and unit.end - unit.start > 40:
                # A function that registers others -- `register(app)` and the
                # handlers inside it -- is read handler by handler.
                out.extend(divide(unit.start, unit.end, node.body, unit.symbol, False,
                                  unit.symbol, unit.kind, depth + 1))
            else:
                out.append(unit)
        return out

    return divide(1, n, tree.body, "", False, "module top", "module", 0)


_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "api_route", "route"}


_JS_ANCHOR = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:"
    r"function\*?\s+(?P<fn>[\w$]+)|class\s+(?P<cls>[\w$]+)|(?:const|let|var)\s+(?P<var>[\w$]+))")
_JS_TEST = re.compile(r"^(?:test|it|describe)(?:\.\w+)?\(\s*(['\"`])(?P<name>.+?)\1")


def _javascript(lines: list[str], test: bool) -> list[Unit]:
    anchors = []
    for i, line in enumerate(lines, 1):
        hit = _JS_ANCHOR.match(line)
        if hit:
            if hit.group("fn"):
                anchors.append((i, i, hit.group("fn"), "function"))
            elif hit.group("cls"):
                anchors.append((i, i, hit.group("cls"), "class"))
            else:
                arrow = "=>" in line or "function" in line
                anchors.append((i, i, hit.group("var"), "function" if arrow else "setting"))
            continue
        hit = _JS_TEST.match(line)
        if hit:
            anchors.append((i, i, hit.group("name")[:80], "test" if test else "function"))
    return _partition(anchors, len(lines), lines, ("//", "/*", "*"))


_CSS_RULE = re.compile(r"^(?![\s}/*@])([^{]+?)\s*\{")


def _css(lines: list[str]) -> list[Unit]:
    anchors = []
    for i, line in enumerate(lines, 1):
        hit = _CSS_RULE.match(line)
        if hit:
            anchors.append((i, i, hit.group(1).strip()[:80], "setting"))
        elif line.startswith("@"):
            anchors.append((i, i, line.split("{")[0].strip()[:80], "setting"))
    return _partition(anchors, len(lines), lines, ("/*", "*"))


_YAML_KEY = re.compile(r"^(?P<indent>\s*)(?P<key>[\w.$/-]+|\"[^\"]+\"|'[^']+')\s*:(?:\s|$)")
_JSON_KEY = re.compile(r"^(?P<indent>\s*)\"(?P<key>[^\"]+)\"\s*:")


def _keyed(lines: list[str], pattern: re.Pattern, comment: tuple[str, ...],
           lo: int = 1, hi: int | None = None, prefix: str = "", depth: int = 0,
           parent: int = -1) -> list[Unit]:
    """Settings files outlined by their keys, a big section divided again by its own."""
    hi = len(lines) if hi is None else hi
    found = []
    for i in range(lo, hi + 1):
        hit = pattern.match(lines[i - 1])
        if hit and len(hit.group("indent").expandtabs()) > parent:
            found.append((i, len(hit.group("indent").expandtabs()), hit.group("key").strip("\"'")))
    if not found:
        return [Unit(prefix or "top", "setting" if prefix else "module", lo, hi)]
    least = min(indent for _, indent, _ in found)
    anchors = [(i, i, f"{prefix}.{key}" if prefix else key, "setting")
               for i, indent, key in found if indent == least]
    units = _partition(anchors, len(lines), lines, comment, top=prefix or "top", lo=lo, hi=hi)
    out: list[Unit] = []
    for unit in units:
        if prefix and unit.symbol == prefix:
            unit.kind = "setting"           # a section's own header line, not a module's top
        inner = [f for f in found if unit.start < f[0] <= unit.end and f[1] > least]
        if depth < 4 and unit.end - unit.start > 30 and inner and unit.kind == "setting":
            out.extend(_keyed(lines, pattern, comment, unit.start, unit.end, unit.symbol, depth + 1,
                              parent=least))
        else:
            out.append(unit)
    return out


_TOML_TABLE = re.compile(r"^\[\[?\s*([^\]]+?)\s*\]\]?")


def _toml(lines: list[str]) -> list[Unit]:
    anchors = [(i, i, m.group(1), "setting") for i, line in enumerate(lines, 1)
               if (m := _TOML_TABLE.match(line))]
    return _partition(anchors, len(lines), lines, ("#",), top="top")


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _markdown(lines: list[str]) -> list[Unit]:
    anchors = [(i, i, m.group(2)[:80], "lines") for i, line in enumerate(lines, 1)
               if (m := _MD_HEADING.match(line))]
    return _partition(anchors, len(lines), lines, (), top="top")


def outline(path: str, text: str, *, test: bool = False) -> list[Unit] | None:
    """Units that partition the file, or None where no outline applies -- the
    file is then cut by where its changes are."""
    lines = text.splitlines()
    if not lines:
        return []
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in (".py", ".pyi"):
        return _python(text, lines, test)
    if suffix in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"):
        return _javascript(lines, test)
    if suffix in (".css", ".scss", ".less"):
        return _css(lines)
    if suffix in (".yaml", ".yml"):
        return _keyed(lines, _YAML_KEY, ("#",))
    if suffix == ".json" and PurePosixPath(path).name not in LOCKFILES:
        return _keyed(lines, _JSON_KEY, ())
    if suffix == ".toml":
        return _toml(lines)
    if suffix in (".md", ".markdown"):
        return _markdown(lines)
    return None


# ==========================================================================
# the cut
# ==========================================================================


def _unit_at(units: list[Unit], n: int) -> int:
    for i, u in enumerate(units):
        if u.start <= n <= u.end:
            return i
    raise LineAccountError(f"line {n} is in no unit of an outline that should cover the file")


def _keys(units: list[Unit]) -> list[tuple[str, int]]:
    """A unit's identity across the two sides: its symbol, and which of that
    name it is, so a symbol that appears twice is still two things."""
    seen: Counter = Counter()
    out = []
    for u in units:
        out.append((u.symbol, seen[u.symbol]))
        seen[u.symbol] += 1
    return out


def _fold(rows: list[list]) -> list[list]:
    """Unchanged runs cut down to the lines either side of a change, and the top
    of the unit, which is where its name is."""
    out: list[list] = []
    i, n = 0, len(rows)
    while i < n:
        if rows[i][0] != " ":
            out.append(rows[i])
            i += 1
            continue
        j = i
        while j < n and rows[j][0] == " ":
            j += 1
        run = rows[i:j]
        # The first lines of a run: after a change, or the unit's own top.
        # The last: just before the next change, when there is one.
        keep, tail = CONTEXT, (CONTEXT if j < n else 0)
        if len(run) > keep + tail + 1:
            out.extend(run[:keep])
            out.append(["~", 0, 0, f"{len(run) - keep - tail} unchanged lines"])
            out.extend(run[len(run) - tail:] if tail else [])
        else:
            out.extend(run)
        i = j
    if len(out) > MAX_ROWS:
        rest = len(out) - MAX_ROWS
        out = out[:MAX_ROWS] + [["~", 0, 0, f"{rest} more rows of this piece, not drawn here"]]
    return out


def _runs(rows: list[list]) -> list[list[int]]:
    """Indexes of changed rows, grouped where few unchanged lines part them."""
    groups: list[list[int]] = []
    gap = RUN_GAP + 1
    for i, row in enumerate(rows):
        if row[0] == " ":
            gap += 1
            continue
        if groups and gap <= RUN_GAP:
            groups[-1].append(i)
        else:
            groups.append([i])
        gap = 0
    return groups


def cut(repo, base: str, head: str, *, tests: Iterable[str] = ()) -> list[Piece]:
    """The branch's diff, every changed line in exactly one piece. Checked."""
    changes = read_diff(repo, base, head)
    known_tests = set(tests)
    pieces: list[Piece] = []
    for change in changes:
        pieces.extend(_cut_file(repo, base, head, change, change.path in known_tests
                                or is_test_path(change.path)))
    for i, piece in enumerate(pieces, 1):
        piece.id = f"P-{i}"
    account(pieces, changes)
    return pieces


def _show(repo, commit: str, path: str) -> str:
    return git.out(["show", f"{commit}:{path}"], repo, timeout=60) or ""


def _cut_file(repo, base: str, head: str, change: FileChange, test: bool) -> list[Piece]:
    name = PurePosixPath(change.path).name
    if change.binary or not change.hunks:
        what = ("binary, changed" if change.binary
                else f"moved from {change.old_path}" if change.status == "R" else "mode changed")
        return [Piece(id="", path=change.path, old_path=change.old_path if change.status == "R" else "",
                      symbol=f"{name} ({what})", kind="file", binary=change.binary, fresh=change.status == "A",
                      **_code_class(change.path, test, moved=change.status == "R" and not change.binary))]
    base_text = _show(repo, base, change.old_path) if change.status != "A" else ""
    head_text = _show(repo, head, change.path) if change.status != "D" else ""
    base_lines, head_lines = base_text.splitlines(), head_text.splitlines()
    rows = rows_of(change, base_lines, head_lines)
    old = change.old_path if change.status == "R" else ""
    if name in LOCKFILES:
        # One piece. A lockfile cut where its changes are is hundreds of
        # pieces of hashes, and none of them was anybody's choice.
        heads = [r[2] for r in rows if r[0] == "+"]
        return [Piece(id="", path=change.path, old_path=old, symbol=name, kind="file", fresh=change.status == "A",
                      start=min(heads) if heads else 0, end=max(heads) if heads else 0,
                      added=heads, removed=[r[1] for r in rows if r[0] == "-"],
                      rows=_fold(rows), **_code_class(change.path, test))]
    head_units = outline(change.path, head_text, test=test) if head_text else []
    base_units = outline(change.old_path, base_text, test=test) if base_text else []

    if head_units is None or base_units is None:
        out = []
        for group in _runs(rows):
            lo, hi = group[0], group[-1]
            span = rows[lo:hi + 1]
            heads = [r[2] for r in span if r[2]]
            bases = [r[1] for r in span if r[1]]
            label = (f"lines {min(heads)}–{max(heads)}" if heads
                     else f"lines {min(bases)}–{max(bases)}, removed")
            out.append(Piece(
                id="", path=change.path, old_path=old, symbol=label, kind="lines", fresh=change.status == "A",
                start=min(heads) if heads else 0, end=max(heads) if heads else 0,
                added=[r[2] for r in span if r[0] == "+"], removed=[r[1] for r in span if r[0] == "-"],
                rows=_fold(rows[max(0, lo - CONTEXT):hi + 1 + CONTEXT]),
                **_code_class(change.path, test)))
        return out

    head_keys, base_keys = _keys(head_units), _keys(base_units)
    order: list[tuple[str, int]] = []
    touched: dict[tuple[str, int], dict] = {}

    def slot(key: tuple[str, int]) -> dict:
        if key not in touched:
            touched[key] = {"added": [], "removed": []}
            order.append(key)
        return touched[key]

    for row in rows:
        if row[0] == "+":
            slot(head_keys[_unit_at(head_units, row[2])])["added"].append(row[2])
        elif row[0] == "-":
            slot(base_keys[_unit_at(base_units, row[1])])["removed"].append(row[1])

    out = []
    for key in order:
        h = head_units[head_keys.index(key)] if key in head_keys else None
        b = base_units[base_keys.index(key)] if key in base_keys else None
        mine = [r for r in rows
                if (h and r[0] in " +" and h.start <= r[2] <= h.end)
                or (b and r[0] == "-" and b.start <= r[1] <= b.end)
                or (not h and b and r[0] == " " and b.start <= r[1] <= b.end)]
        unit = h or b
        assert unit is not None
        symbol = unit.symbol if h else f"{unit.symbol} (removed)"
        if unit.kind == "module" and unit.symbol in ("module top", "top"):
            symbol = f"{name}, top" if h else f"{name}, top (removed)"
        added_here = touched[key]["added"]
        # Whole when the unit is all new: no line of it removed, and every
        # line of it at the head, blank ones aside, one the branch added.
        fresh = bool(h) and not touched[key]["removed"] and all(
            n in set(added_here) or not head_lines[n - 1].strip()
            for n in range(h.start, min(h.end, len(head_lines)) + 1))
        out.append(Piece(
            id="", path=change.path, old_path=old, symbol=symbol, fresh=fresh,
            kind=unit.kind if unit.kind in ("function", "class", "setting", "module", "test", "lines") else "lines",
            start=h.start if h else 0, end=h.end if h else 0,
            added=touched[key]["added"], removed=touched[key]["removed"],
            rows=_fold(mine), **_code_class(change.path, test)))
    out = _whole_when_new(out, head_units, {r[2] for r in rows if r[0] == "+"}, head_lines)
    return _settle_blanks(out, base_lines, head_lines)


def _owner(symbol: str) -> str:
    return re.split(r"[.,]", symbol, maxsplit=1)[0]


def _whole_when_new(pieces: list[Piece], units: list[Unit], added: set[int], head: list[str]) -> list[Piece]:
    """A class the branch added is one piece, not its header, its fields and
    each method apart. Divided, it is divided only to say which part of an old
    class changed; when all of it is new, that question does not arise."""
    spans: dict[str, list[int]] = {}
    for u in units:
        if u.kind == "class" and u.symbol == _owner(u.symbol):
            spans[u.symbol] = [u.start, u.end]
    for u in units:
        span = spans.get(_owner(u.symbol))
        if span and u.symbol != _owner(u.symbol):
            span[1] = max(span[1], u.end)
    out: list[Piece] = []
    for owner, (lo, hi) in spans.items():
        lines = [n for n in range(lo, hi + 1) if n <= len(head) and head[n - 1].strip()]
        if not lines or not all(n in added for n in lines):
            continue
        inside = [p for p in pieces if lo <= p.start <= hi and _owner(p.symbol) == owner and not p.removed]
        if len(inside) < 2:
            continue
        lead = inside[0]
        lead.symbol, lead.kind, lead.fresh = owner, "class", True
        lead.start, lead.end = lo, max(p.end for p in inside)
        lead.added = sorted({n for p in inside for n in p.added})
        lead.rows = [r for p in inside for r in p.rows]
        pieces = [p for p in pieces if p is lead or p not in inside]
    out = pieces
    return out


def _settle_blanks(pieces: list[Piece], base: list[str], head: list[str]) -> list[Piece]:
    """A piece whose only changed lines are blank goes to its neighbour.

    A new class arrives with the two blank lines that separate it, and git
    often puts those on the far side of the class next door -- which would make
    the class next door a piece of its own, holding nothing. Moved, not
    dropped: the neighbour holds them, so every line is still in one piece.
    """
    def blank(p: Piece) -> bool:
        return all(not (head[n - 1] if n <= len(head) else "").strip() for n in p.added) and all(
            not (base[n - 1] if n <= len(base) else "").strip() for n in p.removed)

    out = list(pieces)
    i = 0
    while len(out) > 1 and i < len(out):
        p = out[i]
        if blank(p):
            near = [j for j in (i - 1, i + 1) if 0 <= j < len(out) and not blank(out[j])]
            if near:
                to = out[near[0]]
                to.added = sorted(set(to.added) | set(p.added))
                to.removed = sorted(set(to.removed) | set(p.removed))
                del out[i]
                continue
        i += 1
    return out


def _code_class(path: str, test: bool, *, moved: bool = False) -> dict:
    """The classes code can give without asking anyone."""
    if test:
        return {"klass": "tests", "reason": "In a test file.", "classed_by": "code"}
    if PurePosixPath(path).name in LOCKFILES:
        return {"klass": "generated", "reason": "A lockfile, written by the package manager.",
                "classed_by": "code"}
    if moved:
        return {"klass": "mechanical", "reason": "Moved, with no line changed.", "classed_by": "code"}
    return {"klass": "novel", "reason": UNCLASSED, "classed_by": "default"}


# ==========================================================================
# Every Line Once
# ==========================================================================


def account(pieces: Sequence[Piece], changes: Sequence[FileChange]) -> None:
    """Raise unless every changed line is in exactly one piece, and no piece
    claims a line the diff does not have."""
    wanted = Counter()
    for c in changes:
        wanted.update((c.path, "+", n) for n in c.added)
        wanted.update((c.path, "-", n) for n in c.removed)
    held = Counter()
    for p in pieces:
        held.update((p.path, "+", n) for n in p.added)
        held.update((p.path, "-", n) for n in p.removed)
    missing = sorted(set(wanted) - set(held))
    doubled = sorted(k for k, v in held.items() if v > 1)
    extra = sorted(set(held) - set(wanted))
    if missing or doubled or extra:
        def say(keys):
            return ", ".join(f"{p}:{op}{n}" for p, op, n in keys[:8]) + (" …" if len(keys) > 8 else "")
        raise LineAccountError(
            "the pieces do not hold every changed line exactly once"
            + (f"; in no piece: {say(missing)}" if missing else "")
            + (f"; in two: {say(doubled)}" if doubled else "")
            + (f"; not in the diff: {say(extra)}" if extra else ""))


def account_against(pieces: Sequence[Piece], before: Sequence[Piece]) -> None:
    """The same check, against the cut it was made from."""
    want = Counter()
    for p in before:
        want.update((p.path, "+", n) for n in p.added)
        want.update((p.path, "-", n) for n in p.removed)
    have = Counter()
    for p in pieces:
        have.update((p.path, "+", n) for n in p.added)
        have.update((p.path, "-", n) for n in p.removed)
    if want != have:
        raise LineAccountError("a join lost or doubled a changed line")


# ==========================================================================
# what the rapporteur said, put on the pieces -- and what it may not do
# ==========================================================================


def classify(pieces: list[Piece], judgement: PieceJudgement) -> list[Piece]:
    """Its classes on the pieces code did not class. A piece it skipped, or
    gave no reason for, is novel: unsure means novel (INV-4)."""
    calls = {c.id: c for c in judgement.pieces}
    for piece in pieces:
        if piece.classed_by == "code":
            continue
        call = calls.get(piece.id)
        if call is None:
            piece.klass, piece.reason, piece.classed_by = "novel", UNCLASSED, "default"
        elif not call.reason.strip():
            piece.klass, piece.reason, piece.classed_by = "novel", UNREASONED, "default"
        else:
            piece.klass, piece.reason, piece.classed_by = call.klass, call.reason.strip(), "rapporteur"
    return pieces


def join(pieces: list[Piece], judgement: PieceJudgement) -> tuple[list[Piece], list[dict]]:
    """Joins the rapporteur asked for, where they hold: two or more pieces of one
    file, next to each other in it, none a test, none already joined. The
    joined piece takes the heaviest class among them. Returns the pieces and
    every join refused, with why."""
    index = {p.id: i for i, p in enumerate(pieces)}
    taken: set[str] = set()
    refused: list[dict] = []
    merged: dict[int, Piece] = {}
    gone: set[int] = set()
    for j in judgement.joins:
        ids = list(dict.fromkeys(j.ids))
        why = ""
        if len(ids) < 2:
            why = "a join needs two pieces"
        elif any(i not in index for i in ids):
            why = "it names a piece that does not exist"
        elif any(i in taken for i in ids):
            why = "a piece in it is already joined"
        else:
            members = [pieces[index[i]] for i in ids]
            spots = sorted(index[i] for i in ids)
            if len({m.path for m in members}) != 1:
                why = "the pieces are in different files"
            elif any(m.klass == "tests" for m in members):
                why = "tests are classed by code and are not joined"
            elif spots != list(range(spots[0], spots[0] + len(spots))):
                why = "the pieces are not next to each other"
        if why:
            refused.append({"ids": ids, "reason": j.reason, "refused": why})
            continue
        members = [pieces[i] for i in sorted(index[i] for i in ids)]
        heaviest = min(members, key=lambda m: WEIGHT.index(m.klass) if m.klass in WEIGHT else 0)
        lead = members[0]
        rows: list[list] = []
        for m in members:
            rows.extend(m.rows)
        heads = [m for m in members if m.start]
        merged[index[lead.id]] = Piece(
            id=lead.id, path=lead.path, old_path=lead.old_path,
            symbol=" · ".join(m.symbol for m in members), kind=lead.kind,
            start=min(m.start for m in heads) if heads else 0,
            end=max(m.end for m in heads) if heads else 0,
            added=[n for m in members for n in m.added], removed=[n for m in members for n in m.removed],
            # The class, and the reason for it, of the heaviest piece joined:
            # the reason has to be the one for the class the piece now wears.
            klass=heaviest.klass, reason=heaviest.reason, classed_by=heaviest.classed_by,
            # Added whole only if every part of it was.
            fresh=all(m.fresh for m in members),
            joined=[m.id for m in members], rows=rows[:MAX_ROWS + 1])
        taken.update(ids)
        gone.update(index[i] for i in ids)
    out = []
    for i, p in enumerate(pieces):
        if i in merged:
            out.append(merged[i])
        elif i not in gone:
            out.append(p)
    account_against(out, pieces)
    return out, refused


def _homes(pieces: Sequence[Piece]) -> dict[str, Piece]:
    """Every id a model might name, to the piece it is in now: a piece joined
    into another is found through the piece it was joined into."""
    home = {p.id: p for p in pieces}
    for p in pieces:
        for member in p.joined:
            home[member] = p
    return home


def paths_of(judgement: PieceJudgement, pieces: Sequence[Piece]) -> list[dict]:
    """The rapporteur's paths, each step checked: one that names a piece of
    this branch is `added` or `changed` by what the diff says of that piece,
    never by what the model said; one naming no piece, or a piece that does
    not exist, is outside the branch's code. A path with no title, or no steps
    left, is dropped."""
    home = _homes(pieces)

    def step(s) -> dict | None:
        if not s.label.strip():
            return None
        p = home.get(s.piece.strip())
        if p is None:
            return {"label": s.label.strip(), "note": s.note.strip(), "piece": "", "kind": "outside"}
        return {"label": s.label.strip(), "note": s.note.strip(), "piece": p.id,
                "kind": "added" if p.fresh else "changed", "where": p.path}

    out = []
    for path in judgement.paths:
        steps = [x for x in (step(s) for s in path.steps) if x]
        then_steps = [x for x in (step(s) for s in path.then_steps) if x]
        if not path.title.strip() or not (steps or then_steps):
            continue
        out.append({"title": path.title.strip(), "said": path.said.strip(), "steps": steps,
                    "then": path.then.strip() if then_steps else "", "then_steps": then_steps})
    return out


#: Where a JavaScript suite names a test: vitest, jest, mocha and Playwright.
_JS_TEST_CALLS = ("it", "test", "describe", "test.describe", "it.only", "test.only")


def _test_exists(repo, head: str, name: str) -> bool:
    """Whether a test of this name is in the head: a Python `def`, or a
    JavaScript `it(...)`, `test(...)` or `describe(...)` given it as its title.
    A title is said as the suite says it, spaces and all, and a nested one
    ("TagPicker > closes on Escape") is found by its last part."""
    if re.fullmatch(r"[\w.:-]+", name) and git.out(
            ["grep", "-l", "-w", "-e", f"def {name.split('::')[-1].split('.')[-1]}", head, "--"], repo, timeout=60):
        return True
    title = re.split(r"\s+(?:>|›|::)\s+", name)[-1].strip()
    if not title:
        return False
    patterns = [f"{call}({q}{title}{q}" for call in _JS_TEST_CALLS for q in ("'", '"', "`")]
    args = ["grep", "-l", "-F"]
    for pattern in patterns:
        args += ["-e", pattern]
    return bool(git.out([*args, head, "--"], repo, timeout=60))


def claims_of(judgement: PieceJudgement, repo, head: str, tests_green: bool | None) -> list[dict]:
    """The commit's claims, each against the tests named for it. A test is
    counted only if a function of that name is in the branch's head; a claim
    is `shown` when one is and the project's tests ran green, `unproved` when
    one is but they did not, and `untested` when no test that exists was named."""
    out = []
    for claim in judgement.claims:
        if not claim.quote.strip():
            continue
        named = list(dict.fromkeys(t.strip() for t in claim.tests if t.strip()))
        found = [t for t in named if _test_exists(repo, head, t)]
        state = "untested" if not found else "shown" if tests_green else "unproved"
        out.append({"quote": claim.quote.strip(), "tests": found,
                    "missing": [t for t in named if t not in found], "state": state})
    return out


# ==========================================================================
# what a check found, on the branch's lines or on lines that were already there
# ==========================================================================


def changed_by_file(pieces: Sequence[Piece]) -> dict[str, set[int]]:
    """Head lines the branch added, by file -- from the cut, not a working tree."""
    out: dict[str, set[int]] = {}
    for p in pieces:
        if p.added:
            out.setdefault(p.path, set()).update(p.added)
    return out


def split_located(located: Sequence[Located], lines: dict[str, set[int]]) -> tuple[list[Located], list[Located]]:
    """What a check placed on lines the branch added, and what it placed on
    lines that were there before. A finding on a range counts as the branch's
    when any line of the range is."""
    added, before = [], []
    for loc in located:
        span = range(loc.start_line, max(loc.end_line, loc.start_line) + 1)
        mine = lines.get(loc.path)
        (added if mine and any(n in mine for n in span) else before).append(loc)
    return added, before
