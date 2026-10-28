"""What a branch adds, found in its code rather than asked of anyone.

The part of a branch packet a reader looks at first is what the work adds to
the system, said the way most software is built: the screens and components a
person sees, the endpoints behind them, and the data they keep. Then, only
where a branch has them, what it is worth stopping on: packages it brings in,
what it reaches outside the repository, where it reads or writes a secret, and
settings it adds. Each is a fact a
line of the diff shows, so each is found here, by code, and points at the
piece that holds it. A model is not asked: a list of what a branch reaches for
that a model wrote would be the branch describing itself.

Found by reading, not by running, so it is the floor and not the ceiling: a
secret read through a helper this file has no pattern for is missed, not
invented. What it finds, it can show the line for.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import PurePosixPath
from typing import Any, Sequence

from . import git
from .pieces import LOCKFILES
from .schemas import Piece

_ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|WEBSOCKET|API_ROUTE|ROUTE) (\S+)")
SETTINGS_SUFFIXES = (".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env", ".example")

_ENV = re.compile(r"""os\.environ(?:\.get)?\(\s*["']([A-Z][A-Z0-9_]*)|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)"""
                  r"""|os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)|process\.env\.([A-Z][A-Z0-9_]*)""")
_SECRET_NAME = re.compile(r"TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION", re.I)
_SECRET_CALL = re.compile(r"\b((?:read|write|store|save|load|get|set)_(?:credential|secret|token|api_key|password)s?)\(")
_SECRET_PATH = re.compile(r"""["']([^"'\s]*(?:credential|secret|\.pem|id_rsa|\.netrc|keychain)[^"'\s]*)["']""", re.I)
_OUTSIDE_PATH = re.compile(r"""["']((?:~|\$HOME)/[^"'\s]+|/(?:etc|usr|var|opt|Library)/[^"'\s]+)["']""")
#: The same two, as a settings file writes them: unquoted, after a key or a dash.
_BARE_PATH = re.compile(r"""(?:^|:\s|-\s)\s*((?:~|\$HOME)/\S+|/(?:etc|usr|var|opt|Library)/\S+)""")
_OUTSIDE_CALL = re.compile(r"\b(expanduser|Path\.home|os\.path\.expanduser)\(|\b(npm|git|pip|yarn) config (set|--global)")


def _text(repo, commit: str, path: str) -> str:
    return git.out(["show", f"{commit}:{path}"], repo, timeout=60) or ""


def _added_lines(repo, head: str, pieces: Sequence[Piece]) -> list[tuple[Piece, int, str]]:
    """Every line the branch added, with the piece that holds it."""
    out: list[tuple[Piece, int, str]] = []
    texts: dict[str, list[str]] = {}
    for p in pieces:
        if not p.added or p.binary or p.klass == "tests":
            continue
        lines = texts.setdefault(p.path, _text(repo, head, p.path).splitlines())
        out.extend((p, n, lines[n - 1]) for n in sorted(p.added) if 0 < n <= len(lines))
    return out


def _one(found: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """The first time each thing is found: once per name, at its first line."""
    seen: set[str] = set()
    out = []
    for f in found:
        if f[key] not in seen:
            seen.add(f[key])
            out.append(f)
    return out


def routes(pieces: Sequence[Piece], report: dict | None) -> list[dict[str, Any]]:
    """Ways in the branch added or changed: handlers it cut as `VERB /path`, and
    whatever the as-built credits it with that no handler here already says.

    Not a module's exports, which the as-built lists among its ways in but
    leaves out of every count of them: a constant, a class or a test factory
    is something code imports, not a way in for a person or a client. Nor a
    way in only a test declares."""
    out = []
    for p in pieces:
        m = _ROUTE.match(p.symbol)
        if m and p.klass != "tests":
            out.append({"name": p.symbol, "state": "added" if p.fresh else "changed", "piece": p.id})
    named = {f["name"].split(" ", 1)[-1] for f in out}
    for w in (report or {}).get("ways_in_added") or []:
        if w.get("kind") == "export" or w.get("test"):
            continue
        if w.get("name") and w["name"] not in named:
            out.append({"name": f"{w.get('method') + ' ' if w.get('method') else ''}{w['name']}",
                        "state": "added", "piece": ""})
    return out


def settings(pieces: Sequence[Piece]) -> list[dict[str, Any]]:
    """Keys the branch added to the repository's settings files -- the YAML,
    TOML, JSON and env files a person configures it through."""
    out = []
    for p in pieces:
        name = PurePosixPath(p.path).name
        if p.klass == "tests" or name in LOCKFILES or not name.endswith(SETTINGS_SUFFIXES):
            continue
        if p.kind == "setting" and p.fresh:
            out.append({"name": p.symbol, "file": p.path, "state": "added", "piece": p.id})
    return out


def secrets(added: Sequence[tuple[Piece, int, str]]) -> list[dict[str, Any]]:
    """Where a secret is read or written: a variable named like one, a
    credentials file, a call that stores or loads one."""
    found = []
    for p, n, line in added:
        for m in _ENV.finditer(line):
            name = next(g for g in m.groups() if g)
            if _SECRET_NAME.search(name):
                found.append({"name": f"${name}", "how": "read from the environment", "piece": p.id,
                              "file": p.path, "line": n})
        for m in _SECRET_CALL.finditer(line):
            found.append({"name": f"{m.group(1)}()", "how": "a credential stored or loaded", "piece": p.id,
                          "file": p.path, "line": n})
        for m in _SECRET_PATH.finditer(line):
            found.append({"name": m.group(1), "how": "a credentials file", "piece": p.id, "file": p.path, "line": n})
        for m in _BARE_PATH.finditer(line):
            if _SECRET_PATH.search(f'"{m.group(1)}"'):
                found.append({"name": m.group(1), "how": "a credentials file", "piece": p.id,
                              "file": p.path, "line": n})
    return _one(found, "name")


def outside(added: Sequence[tuple[Piece, int, str]]) -> list[dict[str, Any]]:
    """What the branch reaches outside the repository: a home-folder or system
    path, a home directory looked up, a tool's own configuration set."""
    found = []
    for p, n, line in added:
        for m in [*_OUTSIDE_PATH.finditer(line), *_BARE_PATH.finditer(line)]:
            found.append({"name": m.group(1), "piece": p.id, "file": p.path, "line": n})
        for m in _OUTSIDE_CALL.finditer(line):
            what = f"{m.group(2)} config {m.group(3)}" if m.group(2) else f"{m.group(1)}()"
            found.append({"name": what, "piece": p.id, "file": p.path, "line": n})
    return _one(found, "name")


#: Files a component lives in: a React, Vue or Svelte one is named in these.
FRONT_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte")
#: Capitalised and not shouted: `TagPicker` or `App`, never a constant like `SHOWN`.
_COMPONENT = re.compile(r"^[A-Z][a-z][A-Za-z0-9]*$")


def components(pieces: Sequence[Piece]) -> list[dict[str, Any]]:
    """The screens and components the branch added or changed: what a person
    sees. A component is a capitalised function or class in a front-end file,
    or a Vue or Svelte file itself."""
    found: dict[str, dict[str, Any]] = {}
    for p in pieces:
        path = PurePosixPath(p.path)
        if p.klass == "tests" or path.suffix not in FRONT_SUFFIXES:
            continue
        names = ([path.stem] if path.suffix in (".vue", ".svelte")
                 else [n for n in p.symbol.split(" · ") if _COMPONENT.match(n)])
        for name in names:
            seen = found.get(name)
            state = "added" if p.fresh else "changed"
            if seen is None or (state == "added" and seen["state"] == "changed"):
                found[name] = {"name": name, "file": p.path, "state": state, "piece": p.id}
    if not found:
        # No component files: a page built by plain scripts. Its front-end
        # files, then, which is still what a person sees.
        for p in pieces:
            path = PurePosixPath(p.path)
            if p.klass == "tests" or path.suffix not in PAGE_SUFFIXES:
                continue
            seen = found.get(path.name)
            if seen is None or (p.fresh and seen["state"] == "changed"):
                found[path.name] = {"name": path.name, "file": p.path,
                                    "state": "added" if p.fresh and seen is None else "changed", "piece": p.id}
    return sorted(found.values(), key=lambda c: (c["state"] != "added", c["name"]))


#: Front-end files with no components in them: scripts, pages, stylesheets.
PAGE_SUFFIXES = (".js", ".mjs", ".html", ".css", ".scss")

_MODEL_CALLS = {"mapped_column", "Column", "relationship", "ForeignKey"}


def _lead(lines: Sequence[str], at: int) -> str:
    """The comment a line is written under: the `#`, `//` or `/* */` lines just
    above it, run together, or what follows a `#` on the line itself."""
    out: list[str] = []
    i = at - 1
    if i >= 0 and lines[i].strip().endswith("*/"):
        while i >= 0:
            out.insert(0, lines[i].strip().removeprefix("/**").removeprefix("/*").removesuffix("*/")
                       .removeprefix("*").strip())
            if lines[i].strip().startswith("/*"):
                break
            i -= 1
    else:
        while i >= 0 and lines[i].strip().startswith(("#", "//")):
            out.insert(0, lines[i].strip().lstrip("#:/").strip())
            i -= 1
    if not out and 0 <= at < len(lines) and " # " in lines[at]:
        out = [lines[at].split(" # ", 1)[1]]
    return " ".join(x for x in out if x)


def _docstring(node: ast.AST) -> str:
    doc = ast.get_docstring(node) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
    return " ".join((doc or "").split())


def _column(call: ast.Call, annotation: ast.AST | None, positional_name: bool) -> dict[str, Any]:
    """What a column's own declaration says: its type, its key, whether it may
    be empty, and any comment it carries. Nothing is guessed: what the code
    leaves to the database's default is left blank."""
    args = call.args[1:] if positional_name else call.args
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    fk = next((a for a in args if isinstance(a, ast.Call) and
               getattr(a.func, "id", getattr(a.func, "attr", "")) == "ForeignKey"), None)
    kind = next((a for a in args if a is not fk), None)

    def bare(node: ast.AST) -> str:
        # `String(32)`, `UUID`: the type and its size, not its keyword options.
        if isinstance(node, ast.Call):
            return f"{ast.unparse(node.func)}({', '.join(ast.unparse(a) for a in node.args)})".replace("()", "")
        return ast.unparse(node)

    inner = annotation.slice if isinstance(annotation, ast.Subscript) else None
    optional = inner is not None and ("None" in ast.unparse(inner) or ast.unparse(inner).startswith("Optional"))
    type_ = bare(kind) if kind is not None else ast.unparse(inner).replace(" | None", "") if inner is not None else ""
    true = lambda k: isinstance(kw.get(k), ast.Constant) and kw[k].value is True  # noqa: E731
    keys = []
    if true("primary_key"):
        keys.append("primary key")
    if fk is not None and fk.args and isinstance(fk.args[0], ast.Constant):
        keys.append(f"foreign key to {fk.args[0].value}")
    if true("unique"):
        keys.append("unique")
    nullable = (kw["nullable"].value if isinstance(kw.get("nullable"), ast.Constant)
                else False if true("primary_key")
                else optional if annotation is not None
                else None)
    note = next((str(kw[k].value) for k in ("comment", "doc")
                 if isinstance(kw.get(k), ast.Constant)), "")
    return {"type": type_, "key": ", ".join(keys), "nullable": nullable, "note": note}


def _relation(call: ast.Call, annotation: ast.AST | None) -> dict[str, Any]:
    target = ""
    if call.args and isinstance(call.args[0], ast.Constant):
        target = str(call.args[0].value)
    elif isinstance(annotation, ast.Subscript):
        target = ast.unparse(annotation.slice).strip("\"'")
        target = re.sub(r"^(?:list|List|set|Set)\[[\"']?(.*?)[\"']?\]$", r"\1 (many)", target)
    through = next((k.value for k in call.keywords if k.arg == "secondary"), None)
    via = (f", through {through.value}" if isinstance(through, ast.Constant)
           else f", through {ast.unparse(through)}" if through is not None else "")
    return {"type": f"relation to {target}{via}" if target else "relation", "key": "", "nullable": None, "note": ""}


def _tables(text: str) -> dict[str, dict[str, Any]]:
    """The tables a Python module declares, by name, with their columns: a
    SQLAlchemy model (`__tablename__`) or `Table(...)`, or a Django model.
    Each column carries what its declaration says (`_column`) and the comment
    it is written under; each table, its docstring and its unique constraints."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    lines = text.splitlines()

    def call_name(node: ast.AST) -> str:
        f = node.func if isinstance(node, ast.Call) else None
        return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""

    def uniques(node: ast.AST) -> list[str]:
        return [f"Unique on ({', '.join(str(a.value) for a in c.args if isinstance(a, ast.Constant))})."
                for c in ast.walk(node) if isinstance(c, ast.Call) and call_name(c) == "UniqueConstraint"]

    def described(detail: dict[str, Any], line: int) -> dict[str, Any]:
        return {**detail, "note": detail["note"] or _lead(lines, line - 1)}

    out: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            table = ""
            django = any((b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")) == "Model"
                         for b in node.bases)
            columns: dict[str, str] = {}
            detail: dict[str, dict[str, Any]] = {}
            notes = [_docstring(node)]
            for item in node.body:
                target = (item.target if isinstance(item, ast.AnnAssign) else
                          item.targets[0] if isinstance(item, ast.Assign) and len(item.targets) == 1 else None)
                if not isinstance(target, ast.Name):
                    continue
                value = item.value
                annotation = item.annotation if isinstance(item, ast.AnnAssign) else None
                if target.id == "__tablename__" and isinstance(value, ast.Constant):
                    table = str(value.value)
                elif target.id == "__table_args__" and value is not None:
                    notes += uniques(value)
                elif isinstance(value, ast.Call):
                    name = call_name(value)
                    if name in _MODEL_CALLS or (django and (name.endswith("Field") or name == "ForeignKey")):
                        relation = name == "relationship"
                        columns[target.id] = "relation" if relation else "column"
                        detail[target.id] = described(
                            _relation(value, annotation) if relation else _column(value, annotation, False),
                            item.lineno)
            if django and not table:
                table = node.name.lower()
            if table:
                out[table] = {"owner": node.name, "columns": columns, "detail": detail,
                              "note": " ".join(n for n in notes if n)}
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
              and call_name(node.value) == "Table" and node.value.args
              and isinstance(node.value.args[0], ast.Constant)):
            calls = [a for a in node.value.args[1:] if isinstance(a, ast.Call) and call_name(a) == "Column"
                     and a.args and isinstance(a.args[0], ast.Constant)]
            columns = {str(a.args[0].value): "column" for a in calls}
            detail = {str(a.args[0].value): described(_column(a, None, True), a.lineno) for a in calls}
            owner = node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
            note = " ".join(n for n in [_lead(lines, node.lineno - 1), *uniques(node.value)] if n)
            out[str(node.value.args[0].value)] = {"owner": owner, "columns": columns, "detail": detail,
                                                  "note": note}
    return out


_SQL_TABLE = re.compile(r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[\"`]?(\w+)", re.I)
_SQL_COLUMN = re.compile(r"\balter\s+table\s+[\"`]?(\w+)[\"`]?\s+add\s+(?:column\s+)?[\"`]?(\w+)", re.I)
_OP_TABLE = re.compile(r"""\bop\.create_table\(\s*["'](\w+)""")
_OP_COLUMN = re.compile(r"""\bop\.add_column\(\s*["'](\w+)["']\s*,\s*sa\.Column\(\s*["'](\w+)""")
_PRISMA_MODEL = re.compile(r"^\s*model\s+(\w+)\s*\{")


def data(repo, base: str, head: str, pieces: Sequence[Piece],
         added: Sequence[tuple[Piece, int, str]]) -> dict[str, Any]:
    """Tables the branch adds, with their columns, and columns it adds to (or
    takes from) tables that were there: read from the models at both ends,
    and from the migrations and schema files it adds."""
    made: dict[str, dict[str, Any]] = {}
    changed: dict[str, dict[str, Any]] = {}
    gone: list[str] = []

    def piece_for(path: str, *names: str) -> str:
        for p in pieces:
            if p.path == path and any(n and n in p.symbol for n in names):
                return p.id
        return next((p.id for p in pieces if p.path == path), "")

    for path in sorted({p.path for p in pieces if p.path.endswith(".py") and p.klass != "tests"}):
        before, after = _tables(_text(repo, base, path)), _tables(_text(repo, head, path))
        for name, t in after.items():
            if name not in before:
                made[name] = {"name": name, "columns": sorted(c for c, k in t["columns"].items() if k == "column"),
                              "relations": sorted(c for c, k in t["columns"].items() if k == "relation"),
                              "detail": t["detail"], "note": t["note"],
                              "piece": piece_for(path, t["owner"], name)}
                continue
            plus = sorted(set(t["columns"]) - set(before[name]["columns"]))
            minus = sorted(set(before[name]["columns"]) - set(t["columns"]))
            if plus or minus:
                changed[name] = {"name": name,
                                 "added": [c for c in plus if t["columns"][c] == "column"],
                                 "relations": [c for c in plus if t["columns"][c] == "relation"],
                                 "removed": minus, "note": t["note"],
                                 "detail": {**{c: before[name]["detail"][c] for c in minus},
                                            **{c: t["detail"][c] for c in plus}},
                                 "piece": piece_for(path, t["owner"], name)}
        gone += [n for n in before if n not in after]
    for p, _n, line in added:
        for m in [*_SQL_TABLE.finditer(line), *_OP_TABLE.finditer(line)]:
            made.setdefault(m.group(1), {"name": m.group(1), "columns": [], "relations": [],
                                         "detail": {}, "note": "", "piece": p.id})
        for m in [*_SQL_COLUMN.finditer(line), *_OP_COLUMN.finditer(line)]:
            if m.group(1) not in made:
                entry = changed.setdefault(m.group(1), {"name": m.group(1), "added": [], "relations": [],
                                                        "removed": [], "detail": {}, "note": "",
                                                        "piece": p.id})
                if m.group(2) not in entry["added"]:
                    entry["added"].append(m.group(2))
        if p.path.endswith(".prisma"):
            m = _PRISMA_MODEL.match(line)
            if m:
                made.setdefault(m.group(1), {"name": m.group(1), "columns": [], "relations": [],
                                             "detail": {}, "note": "", "piece": p.id})
    return {"tables_added": sorted(made.values(), key=lambda t: t["name"]),
            "tables_changed": sorted(changed.values(), key=lambda t: t["name"]),
            "tables_removed": sorted(set(gone))}


#: A path in quotes, or in a template literal whose `${...}` may hold a call.
_PATH_LITERAL = re.compile(r"""['"](/[A-Za-z0-9_{}./:-]*[A-Za-z][A-Za-z0-9_{}./:-]*)['"]|`(/[^`]*)`""")
_METHOD = re.compile(r"""method:\s*[`'"](\w+)""", re.I)
_HELPER = re.compile(r"(?:^|\s)(?:async\s+)?(?:function\s+)?(\w+)\s*(?::|=)\s*(?:async\s*)?\(|function\s+(\w+)\s*\(")


def _same_route(literal: str, path: str) -> bool:
    """A path the front end names, against a route's: placeholders on either
    side match any one segment."""
    a = [x for x in literal.split("?")[0].strip("/").split("/")]
    b = [x for x in path.strip("/").split("/")]
    return len(a) == len(b) and all(x == y or x.startswith("{") or y.startswith("{") for x, y in zip(a, b))


def _units(repo, head: str, path: str) -> tuple[list[str], list]:
    from .pieces import outline
    text = _text(repo, head, path)
    return text.splitlines(), (outline(path, text) or [])


def links(repo, head: str, pieces: Sequence[Piece], found: dict[str, Any]) -> dict[str, Any]:
    """Which screens call which endpoints, and which endpoints touch which
    data: read from the code at the head, as the spec's map is drawn from what
    the spec writer declared. A front-end file that names an endpoint's path
    calls it -- through the component the call is in, or, from a shared client,
    through every component that calls that client's function. A handler that
    names a model or a table touches it, and so does a helper of its own file
    it calls."""
    routes = [r["name"].split(" ", 1) for r in found["routes"] if " " in r["name"]]
    drawn_ui = {c["name"] for c in found["components"]}
    ui_edges: set[tuple[str, str]] = set()
    front = sorted({p.path for p in pieces if PurePosixPath(p.path).suffix in (*FRONT_SUFFIXES, ".ts", ".js")
                    and p.klass != "tests"})
    # Every front-end file at the head that a component might call through.
    listed = git.out(["ls-tree", "-r", "--name-only", head], repo, timeout=60) or ""
    front_all = [f for f in listed.splitlines() if PurePosixPath(f).suffix in (*FRONT_SUFFIXES, ".ts", ".js", ".mjs")
                 and "test" not in f and not f.endswith(".d.ts") and "node_modules" not in f]
    texts = {f: _units(repo, head, f) for f in front_all}

    def component_units() -> list[tuple[str, Any, str]]:
        out = []
        for f in front:
            lines, units = texts.get(f) or _units(repo, head, f)
            for u in units:
                for name in u.symbol.split(" · "):
                    if name in drawn_ui:
                        out.append((name, u, "\n".join(lines[u.start - 1:u.end])))
        return out

    comps = component_units()
    for f in front_all:
        lines, units = texts[f]
        for n, line in enumerate(lines, 1):
            for m in _PATH_LITERAL.finditer(line):
                literal = re.sub(r"\$\{[^}]*\}", "{}", m.group(1) or m.group(2))
                # The method, from the call the path is in: up to the next
                # function, never into it -- or the next one's POST is this GET's.
                ahead = [line[m.end():]]
                for nxt in lines[n:n + 4]:
                    if _HELPER.search(nxt):
                        break
                    ahead.append(nxt)
                said = _METHOD.search("\n".join(ahead))
                method = (said.group(1).upper() if said else "GET")
                hits = [f"{verb} {path}" for verb, path in routes if verb == method and _same_route(literal, path)]
                if not hits:
                    continue
                unit = next((u for u in units if u.start <= n <= u.end), None)
                names = [x for x in (unit.symbol.split(" · ") if unit else []) if x in drawn_ui]
                callers = set(names)
                if not callers:
                    helper = None
                    for k in range(n, max(n - 6, 0), -1):
                        h = _HELPER.search(lines[k - 1])
                        if h:
                            helper = h.group(1) or h.group(2)
                            break
                    if helper:
                        callers = {c for c, _u, body in comps if re.search(rf"\b{re.escape(helper)}\(", body)}
                ui_edges |= {(c, r) for c in callers for r in hits}

    # Endpoints to data: what each handler, and the helpers it calls, names.
    owners: dict[str, str] = {}
    models = git.out(["grep", "-l", "-e", "__tablename__", "-e", "Table(", "-e", "models.Model", head, "--", "*.py"],
                     repo, timeout=60) or ""
    for entry in models.splitlines():
        path = entry.split(":", 1)[-1]
        for table, t in _tables(_text(repo, head, path)).items():
            if t["owner"]:
                owners[t["owner"]] = table
    data_edges: set[tuple[str, str]] = set()
    by_file: dict[str, tuple[list[str], list]] = {}
    for p in pieces:
        name = p.symbol
        if not _ROUTE.match(name) or not p.path.endswith(".py"):
            continue
        lines, units = by_file.setdefault(p.path, _units(repo, head, p.path))
        unit = next((u for u in units if u.symbol == name), None)
        if unit is None:
            continue
        body = "\n".join(lines[unit.start - 1:unit.end])
        for u in units:
            short = u.symbol.split(".")[-1]
            if u is not unit and re.fullmatch(r"\w+", short) and re.search(rf"\b{re.escape(short)}\(", body):
                body += "\n" + "\n".join(lines[u.start - 1:u.end])
        for owner, table in owners.items():
            # Used, not merely named: `Tag.name`, `select(Card...)`,
            # `insert(card_tags)`. A type annotation -- `workspace: Workspace`
            # -- reads nothing from the table.
            used = rf"\b{re.escape(owner)}\s*[.(]|\b(?:select|delete|insert|update|join|query)\(\s*{re.escape(owner)}\b"
            if re.search(used, body):
                data_edges.add((name, table))
    touched = sorted({t for _r, t in data_edges}
                     - {t["name"] for t in found["data"]["tables_added"]}
                     - {t["name"] for t in found["data"]["tables_changed"]})
    return {"ui_endpoint": [list(e) for e in sorted(ui_edges)],
            "endpoint_data": [list(e) for e in sorted(data_edges)], "tables_used": touched}


def _manifest_names(path: str, text: str) -> set[str]:
    """The direct dependencies a manifest names, by package name."""
    name = PurePosixPath(path).name
    try:
        if name == "package.json":
            data = json.loads(text or "{}")
            return {k for sec in ("dependencies", "devDependencies", "peerDependencies",
                                  "optionalDependencies") for k in (data.get(sec) or {})}
        if name == "pyproject.toml":
            data = tomllib.loads(text or "")
            project = data.get("project") or {}
            specs = list(project.get("dependencies") or [])
            for group in (project.get("optional-dependencies") or {}).values():
                specs += list(group)
            return {re.split(r"[\s<>=!~;\[(]", s.strip(), maxsplit=1)[0].lower() for s in specs if s.strip()}
        if re.match(r"requirements.*\.txt$", name):
            return {re.split(r"[\s<>=!~;\[(]", ln.strip(), maxsplit=1)[0].lower()
                    for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "-"))}
    except (ValueError, tomllib.TOMLDecodeError):
        return set()
    return set()


def packages(repo, base: str, head: str, pieces: Sequence[Piece]) -> dict[str, Any]:
    """Packages the branch's manifests added or dropped, and whether a
    lockfile moved -- which says the installed set changed even when no
    manifest line did."""
    added: set[str] = set()
    removed: set[str] = set()
    for path in sorted({p.path for p in pieces}):
        name = PurePosixPath(path).name
        if name in ("package.json", "pyproject.toml") or re.match(r"requirements.*\.txt$", name):
            before, after = (_manifest_names(path, _text(repo, base, path)),
                             _manifest_names(path, _text(repo, head, path)))
            added |= after - before
            removed |= before - after
    locks = sorted({p.path for p in pieces if PurePosixPath(p.path).name in LOCKFILES})
    return {"added": sorted(added), "removed": sorted(removed), "lockfiles": locks}


def described(repo, head: str, pieces: Sequence[Piece], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each found thing with what its own code says it is for: a Python
    function's docstring, or the comment a component is written under."""
    by_id = {p.id: p for p in pieces}
    texts: dict[str, str] = {}
    out = []
    for f in found:
        p = by_id.get(f.get("piece", ""))
        note = ""
        if p is not None and p.start:
            text = texts.setdefault(p.path, _text(repo, head, p.path))
            if p.path.endswith(".py"):
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError):
                    tree = None
                # A piece starts at the blank lines before its unit, so the
                # function is the first one that begins inside it.
                spans = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and p.start <= min([n.lineno, *(d.lineno for d in n.decorator_list)]) <= (p.end or p.start)
                         ] if tree else []
                note = _docstring(min(spans, key=lambda n: n.lineno)) if spans else ""
            else:
                lines = text.splitlines()
                at = next((i for i, line in enumerate(lines) if re.search(
                    rf"\b(?:function|class|const|let)\s+{re.escape(f['name'])}\b", line)), -1)
                note = _lead(lines, at) if at >= 0 else ""
        out.append({**f, "note": note})
    return out


def facts(repo, base: str, head: str, pieces: Sequence[Piece], report: dict | None) -> dict[str, Any]:
    added = _added_lines(repo, head, pieces)
    found = {
        "components": described(repo, head, pieces, components(pieces)),
        "routes": described(repo, head, pieces, routes(pieces, report)),
        "data": data(repo, base, head, pieces, added),
    }
    return {
        **found,
        "links": links(repo, head, pieces, found),
        "settings": settings(pieces),
        "secrets": secrets(added),
        "outside": outside(added),
        "packages": packages(repo, base, head, pieces),
    }
