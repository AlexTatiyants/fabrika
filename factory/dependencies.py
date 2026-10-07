"""What a project depends on, what a feature changed about it, and what is known about each.

A worker may add a dependency. What it may not do is add one nobody sees: a
package that is known to be vulnerable, one published to hijack the name of
another, one released yesterday by an account that was taken over last
night, or one whose license the project cannot ship under. None of that is a
judgement a model should be asked for, and none of it is in the worker's own
account of what it did. It is in the lockfile, and in three public records
about what the lockfile names.

So this module reads lockfiles -- never manifests alone, because a manifest
names a package and not the forty it brings with it -- and diffs them against
the base commit. Then it asks OSV what is known against each new version,
asks the package's registry when it was published and under what license,
and holds the answers to a policy a person set.

Two lines it never crosses. A package from a private registry is never named
to a public service: its name may be the secret. And a lookup that failed is
never an answer: a package nobody could look up is "not checked", not clean.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml

from . import git

#: The lockfiles read, by file name. Each is a fact about one ecosystem.
LOCKFILES = {
    "uv.lock": "PyPI",
    "poetry.lock": "PyPI",
    "requirements.txt": "PyPI",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "npm",
    "yarn.lock": "npm",
}

#: Where a direct dependency is declared, by ecosystem.
MANIFESTS = {"pyproject.toml": "PyPI", "package.json": "npm"}

#: Registries whose packages are public. Anything resolved from elsewhere is
#: private, and its name stays on this machine.
PUBLIC_SOURCES = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org",
                  "registry.yarnpkg.com")


@dataclass
class Locked:
    """One package at one version, as a lockfile pins it."""

    ecosystem: str
    name: str
    version: str
    direct: bool = False
    #: The direct dependency that brought this one in; empty for a direct one.
    via: str = ""
    #: Empty when it came from a public registry; otherwise where it came from.
    private: str = ""
    lockfile: str = ""


def normalise(ecosystem: str, name: str) -> str:
    """A package's name as its registry compares it."""
    name = (name or "").strip()
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "PyPI" else name


def _private(source: str) -> str:
    source = (source or "").strip()
    if not source or any(host in source for host in PUBLIC_SOURCES):
        return ""
    return source


def _attach_via(found: dict[str, Locked], graph: Mapping[str, Iterable[str]]) -> None:
    """Name, for each transitive package, the direct one it arrived through.

    Breadth first from the direct dependencies, so the shortest route wins --
    the one a person would follow to find out why a package is here.
    """
    seen: set[str] = set()
    queue = [(n, n) for n, p in found.items() if p.direct]
    while queue:
        name, root = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        pkg = found.get(name)
        if pkg is not None and not pkg.direct and not pkg.via:
            pkg.via = found[root].name if root in found else root
        for child in graph.get(name, ()):
            if child not in seen:
                queue.append((child, root))


# -- parsers -------------------------------------------------------------------
#
# Each takes the lockfile's text and the names its manifest declares, and
# returns every package it pins. A file that will not parse returns None,
# which the caller reports; it is never an empty project.


def parse_uv(text: str, declared: set[str]) -> list[Locked] | None:
    try:
        data = tomllib.loads(text)
    except ValueError:
        return None
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    roots: set[str] = set()
    for pkg in data.get("package") or []:
        name = normalise("PyPI", pkg.get("name", ""))
        source = pkg.get("source") or {}
        deps = [normalise("PyPI", d.get("name", "")) for d in pkg.get("dependencies") or []]
        for extra in (pkg.get("optional-dependencies") or {}).values():
            deps += [normalise("PyPI", d.get("name", "")) for d in extra]
        for group in (pkg.get("dev-dependencies") or {}).values():
            deps += [normalise("PyPI", d.get("name", "")) for d in group]
        if "virtual" in source or "editable" in source:
            roots.update(deps)                     # the project itself
            continue
        graph[name] = deps
        found[name] = Locked("PyPI", name, str(pkg.get("version", "")),
                             private=_private(source.get("registry") or source.get("url")
                                              or source.get("git") or ""))
    for name in roots | declared:
        if name in found:
            found[name].direct = True
    _attach_via(found, graph)
    return list(found.values())


def parse_poetry(text: str, declared: set[str]) -> list[Locked] | None:
    try:
        data = tomllib.loads(text)
    except ValueError:
        return None
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    for pkg in data.get("package") or []:
        name = normalise("PyPI", pkg.get("name", ""))
        source = pkg.get("source") or {}
        graph[name] = [normalise("PyPI", d) for d in (pkg.get("dependencies") or {})]
        found[name] = Locked("PyPI", name, str(pkg.get("version", "")),
                             private=_private(source.get("url", "")),
                             direct=name in declared)
    _attach_via(found, graph)
    return list(found.values())


_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;#\\]+)")


def parse_requirements(text: str, declared: set[str]) -> list[Locked] | None:
    """Pinned requirements, as pip-tools writes them: `name==version`, then
    `# via` lines naming what asked for it. Unpinned lines lock nothing and are
    left out. An index other than the public one makes every name private."""
    index = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("--index-url", "--extra-index-url", "-i ")):
            index = index or (stripped.split(None, 1)[1] if " " in stripped
                              else stripped.split("=", 1)[-1])
    private = _private(index)
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        pin = _PIN.match(line.strip())
        if pin:
            current = normalise("PyPI", pin.group(1))
            found[current] = Locked("PyPI", current, pin.group(2), private=private)
            continue
        via = re.match(r"^\s*#\s+(?:via\s+)?(.+)$", line)
        if current and via and line.startswith((" ", "\t")):
            for parent in re.split(r"[\s,]+", via.group(1).strip()):
                if parent.startswith("-r") or parent.endswith((".in", ".txt", ".toml")):
                    found[current].direct = True
                elif parent and parent != "via":
                    graph.setdefault(normalise("PyPI", parent), []).append(current)
    for name in declared:
        if name in found:
            found[name].direct = True
    if not any(p.direct for p in found.values()):
        for p in found.values():                   # hand-pinned: every line is a choice
            p.direct = True
    _attach_via(found, graph)
    return list(found.values())


def parse_package_lock(text: str, declared: set[str]) -> list[Locked] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return None
    root = packages.get("") or {}
    direct = set(declared)
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        direct.update((root.get(key) or {}).keys())
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    for path, pkg in packages.items():
        if not path or "node_modules/" not in path or pkg.get("link"):
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        nested = path.count("node_modules/") > 1
        key = name if not nested else f"{name}@{pkg.get('version', '')}"
        graph.setdefault(name, []).extend((pkg.get("dependencies") or {}).keys())
        found[key] = Locked("npm", name, str(pkg.get("version", "")),
                            direct=(name in direct and not nested),
                            private=_private(pkg.get("resolved", "")))
    _attach_via(found, graph)
    return list(found.values())


def parse_pnpm(text: str, declared: set[str]) -> list[Locked] | None:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    direct = set(declared)
    importers = data.get("importers") or {".": data}
    for importer in importers.values():
        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            direct.update((importer or {}).get(key) or {})
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    for key, pkg in ((data.get("snapshots") or data.get("packages") or {})).items():
        spec = str(key).lstrip("/").split("(")[0]
        name, _, version = spec.rpartition("@")
        if not name:
            continue
        graph.setdefault(name, []).extend(((pkg or {}).get("dependencies") or {}).keys())
        resolution = ((data.get("packages") or {}).get(key) or pkg or {}).get("resolution") or {}
        found[spec] = Locked("npm", name, version, direct=name in direct,
                             private=_private(resolution.get("tarball", "")))
    _attach_via({p.name: p for p in found.values()}, graph)
    return list(found.values())


def parse_yarn(text: str, declared: set[str]) -> list[Locked] | None:
    """Yarn's own format (v1), or the YAML its later versions write."""
    found: dict[str, Locked] = {}
    graph: dict[str, list[str]] = {}
    current: Locked | None = None
    in_deps = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            first = line.rstrip(":").split(",")[0].strip().strip('"')
            name = first[: first.rindex("@")] if "@" in first[1:] else first
            current = Locked("npm", name, "", direct=name in declared)
            in_deps = False
            continue
        if current is None:
            continue
        body = line.strip()
        if body.startswith(("version ", "version:")):
            current.version = body.split(None, 1)[1].strip().strip('"')
            found[f"{current.name}@{current.version}"] = current
        elif body.startswith(("resolved ", "resolution:")):
            current.private = _private(body.split(None, 1)[1])
        elif body.rstrip(":") == "dependencies":
            in_deps = True
        elif in_deps and line.startswith("    "):
            graph.setdefault(current.name, []).append(body.split()[0].strip('"').rstrip(":"))
        else:
            in_deps = False
    by_name = {p.name: p for p in found.values()}
    _attach_via(by_name, graph)
    return list(found.values())


PARSERS: dict[str, Callable[[str, set[str]], list[Locked] | None]] = {
    "uv.lock": parse_uv, "poetry.lock": parse_poetry, "requirements.txt": parse_requirements,
    "package-lock.json": parse_package_lock, "pnpm-lock.yaml": parse_pnpm,
    "yarn.lock": parse_yarn,
}


def declared_names(manifest: str, text: str | None) -> set[str]:
    """The names a manifest declares directly. Empty for one that is absent or broken."""
    if not text:
        return set()
    try:
        if manifest == "package.json":
            data = json.loads(text)
            return {n for key in ("dependencies", "devDependencies", "optionalDependencies",
                                  "peerDependencies") for n in (data.get(key) or {})}
        data = tomllib.loads(text)
    except ValueError:
        return set()
    names: list[str] = []
    project = data.get("project") or {}
    names += project.get("dependencies") or []
    for extra in (project.get("optional-dependencies") or {}).values():
        names += extra
    for group in (data.get("dependency-groups") or {}).values():
        names += [g for g in group if isinstance(g, str)]
    poetry = (data.get("tool") or {}).get("poetry") or {}
    names += list((poetry.get("dependencies") or {}).keys())
    for group in (poetry.get("group") or {}).values():
        names += list(((group or {}).get("dependencies") or {}).keys())
    out = set()
    for raw in names:
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(raw))
        if match and match.group(1).lower() != "python":
            out.add(normalise("PyPI", match.group(1)))
    return out


@dataclass
class Inventory:
    """Everything the lockfiles of one tree pin, and what could not be read."""

    packages: list[Locked] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    #: Directly declared names, by ecosystem, from the manifests.
    declared: dict[str, set[str]] = field(default_factory=dict)
    #: Ecosystems with a manifest and no lockfile: only direct names are known.
    unlocked: list[str] = field(default_factory=list)


def read_inventory(paths: Sequence[str], read: Callable[[str], str | None]) -> Inventory:
    """Read every lockfile and manifest among `paths`, with `read` for the text.

    `read` is how the caller reaches a tree: a file on disk for the head, `git
    show` for the base. Lockfiles in a dependency directory are someone else's
    and are skipped.
    """
    inv = Inventory()
    by_dir: dict[str, dict[str, str]] = {}
    for path in paths:
        pure = PurePosixPath(path)
        if any(part in ("node_modules", ".venv", "venv", "vendor") for part in pure.parts):
            continue
        if pure.name in LOCKFILES or pure.name in MANIFESTS:
            by_dir.setdefault(str(pure.parent), {})[pure.name] = path
    for directory, files in sorted(by_dir.items()):
        declared: dict[str, set[str]] = {}
        for manifest, ecosystem in MANIFESTS.items():
            if manifest in files:
                declared.setdefault(ecosystem, set()).update(
                    declared_names(manifest, read(files[manifest])))
        for eco, names in declared.items():
            inv.declared.setdefault(eco, set()).update(names)
        locked_here: set[str] = set()
        for lockfile, ecosystem in LOCKFILES.items():
            if lockfile not in files:
                continue
            text = read(files[lockfile])
            parsed = PARSERS[lockfile](text or "", declared.get(ecosystem, set())) \
                if text is not None else None
            if parsed is None:
                inv.unreadable.append(files[lockfile])
                continue
            locked_here.add(ecosystem)
            inv.lockfiles.append(files[lockfile])
            for pkg in parsed:
                pkg.lockfile = files[lockfile]
            inv.packages += parsed
        for ecosystem in declared:
            if ecosystem not in locked_here and declared[ecosystem]:
                manifest = next(m for m, e in MANIFESTS.items() if e == ecosystem)
                inv.unlocked.append(str(PurePosixPath(directory) / manifest))
    return inv


@dataclass
class Change:
    """One package this feature added, moved or removed."""

    ecosystem: str
    name: str
    kind: str                        # added | upgraded | downgraded | changed | removed
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    direct: bool = False
    via: str = ""
    private: str = ""
    lockfile: str = ""


def _version_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.+-]", version))


def diff(base: Inventory, head: Inventory) -> list[Change]:
    """What the lockfiles pin now that they did not at the base, and the reverse."""
    def index(inv: Inventory) -> dict[tuple[str, str], list[Locked]]:
        out: dict[tuple[str, str], list[Locked]] = {}
        for pkg in inv.packages:
            out.setdefault((pkg.ecosystem, pkg.name), []).append(pkg)
        return out

    was, now = index(base), index(head)
    changes: list[Change] = []
    for key in sorted(set(was) | set(now)):
        before = sorted({p.version for p in was.get(key, [])})
        after = sorted({p.version for p in now.get(key, [])})
        if before == after:
            continue
        ref = (now.get(key) or was.get(key))[0]
        direct = any(p.direct for p in now.get(key, []))
        if not before:
            kind = "added"
        elif not after:
            kind = "removed"
        elif len(before) == len(after) == 1:
            kind = "upgraded" if _version_key(after[0]) > _version_key(before[0]) else "downgraded"
        else:
            kind = "changed"
        changes.append(Change(ref.ecosystem, ref.name, kind, before, after, direct,
                              "" if direct else ref.via, ref.private, ref.lockfile))
    return changes


def unresolved(base: Inventory, head: Inventory) -> list[tuple[str, str]]:
    """(ecosystem, name) declared directly at the head, new since the base, and
    pinned by no lockfile -- where the ecosystem has a lockfile at all. A
    project with none is told once at gate 0, not once per package."""
    locked = {(p.ecosystem, p.name) for p in head.packages}
    has_lock = {p.ecosystem for p in head.packages}
    out = []
    for eco, names in head.declared.items():
        if eco not in has_lock:
            continue
        for name in sorted(names - base.declared.get(eco, set())):
            if (eco, name) not in locked:
                out.append((eco, name))
    return out


# -- what is known about a package ----------------------------------------------

OSV_BATCH = "https://api.osv.dev/v1/querybatch"

#: Trove classifiers, which most Python packages still use instead of SPDX,
#: as the SPDX identifier they mean. Only the common ones; anything else is
#: reported as unknown rather than guessed at.
CLASSIFIER_SPDX = {
    "MIT License": "MIT", "Apache Software License": "Apache-2.0", "BSD License": "BSD",
    "ISC License (ISCL)": "ISC", "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0", "The Unlicense (Unlicense)": "Unlicense",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "GNU General Public License v3 or later (GPLv3+)": "GPL-3.0-or-later",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "GNU General Public License (GPL)": "GPL",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.0-or-later",
    "GNU Affero General Public License v3": "AGPL-3.0",
    "GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0-or-later",
    "Zope Public License": "ZPL-2.1", "Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
}


@dataclass
class Facts:
    """What three public records say about one package at one version."""

    checked: bool = False
    license: str = ""
    published: str = ""
    advisories: list[str] = field(default_factory=list)
    malicious: list[str] = field(default_factory=list)


def spdx_from_pypi(info: Mapping[str, Any]) -> str:
    """An SPDX expression for a PyPI release, or "" when it says nothing usable."""
    expression = str(info.get("license_expression") or "").strip()
    if expression:
        return expression
    for classifier in info.get("classifiers") or []:
        parts = [p.strip() for p in str(classifier).split("::")]
        if len(parts) >= 3 and parts[0] == "License" and parts[-1] in CLASSIFIER_SPDX:
            return CLASSIFIER_SPDX[parts[-1]]
    short = str(info.get("license") or "").strip()
    return short if short and len(short) <= 40 and "\n" not in short else ""


def spdx_from_npm(meta: Mapping[str, Any]) -> str:
    value = meta.get("license")
    if isinstance(value, dict):
        value = value.get("type")
    if not value and isinstance(meta.get("licenses"), list) and meta["licenses"]:
        first = meta["licenses"][0]
        value = first.get("type") if isinstance(first, dict) else first
    return str(value or "").strip()


class Lookup:
    """Asks OSV and the registries. Swapped for a stub in tests; nothing in the
    suite reaches the network. Run by Fabrika itself, outside every container,
    because it needs the network and names only public packages."""

    def __init__(self, timeout_s: float = 20.0, concurrency: int = 8):
        self.timeout_s = timeout_s
        self.concurrency = concurrency

    async def facts(
        self, packages: Sequence[tuple[str, str, str]], *, dates: bool = True,
    ) -> dict[tuple[str, str, str], Facts]:
        """OSV for every package, then each registry for license and date.

        `dates=False` for a whole inventory: release age only matters for what a
        feature adds, and the npm record that carries dates is the package's
        entire history -- megabytes for a popular one, times hundreds."""
        import asyncio

        import httpx

        out = {key: Facts() for key in packages}
        if not packages:
            return out
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            results: list[Any] = []
            try:
                for start in range(0, len(packages), 1000):
                    chunk = packages[start:start + 1000]
                    response = await client.post(OSV_BATCH, json={"queries": [
                        {"package": {"ecosystem": eco, "name": name}, "version": version}
                        for eco, name, version in chunk]})
                    response.raise_for_status()
                    results += response.json().get("results") or []
            except (httpx.HTTPError, ValueError):
                return out                     # OSV unreachable: nothing is checked
            osv_ok = len(results) == len(packages)
            for key, result in zip(packages, results):
                ids = [v.get("id", "") for v in (result or {}).get("vulns") or []]
                out[key].malicious = [i for i in ids if i.startswith("MAL-")]
                out[key].advisories = [i for i in ids if not i.startswith("MAL-")]

            gate = asyncio.Semaphore(self.concurrency)

            async def registry(key: tuple[str, str, str]) -> None:
                eco, name, version = key
                async with gate:
                    try:
                        if eco == "PyPI":
                            r = await client.get(f"https://pypi.org/pypi/{name}/{version}/json")
                            r.raise_for_status()
                            data = r.json()
                            out[key].license = spdx_from_pypi(data.get("info") or {})
                            times = [u.get("upload_time_iso_8601") or "" for u in data.get("urls") or []]
                            out[key].published = min((t for t in times if t), default="")
                        elif dates:
                            r = await client.get(f"https://registry.npmjs.org/{name}")
                            r.raise_for_status()
                            data = r.json()
                            out[key].license = spdx_from_npm(
                                (data.get("versions") or {}).get(version) or {})
                            out[key].published = str((data.get("time") or {}).get(version) or "")
                        else:
                            r = await client.get(f"https://registry.npmjs.org/{name}/{version}")
                            r.raise_for_status()
                            out[key].license = spdx_from_npm(r.json())
                        out[key].checked = osv_ok
                    except (httpx.HTTPError, ValueError):
                        out[key].checked = False

            await asyncio.gather(*(registry(k) for k in packages))
        return out


def license_verdict(expression: str, deny: Sequence[str], flag: Sequence[str]) -> str:
    """`denied`, `flagged`, `unknown` or `` for an SPDX expression.

    `A OR B` is the licensee's choice, so it is as bad as its best alternative;
    `A AND B` binds both, so it is as bad as its worst.
    """
    text = (expression or "").strip()
    if not text or text.upper() in ("UNKNOWN", "UNLICENSED", "SEE LICENSE IN LICENSE"):
        return "unknown"
    rank = {"": 0, "flagged": 1, "denied": 2}

    def one(token: str) -> str:
        token = token.strip("() ").split(" WITH ")[0].strip()
        if any(fnmatch(token, p) for p in deny):
            return "denied"
        if any(fnmatch(token, p) for p in flag):
            return "flagged"
        return ""

    choices = []
    for alternative in re.split(r"\s+OR\s+", text.strip("()"), flags=re.I):
        parts = [one(t) for t in re.split(r"\s+AND\s+", alternative, flags=re.I)]
        choices.append(max(parts, key=rank.__getitem__))
    return min(choices, key=rank.__getitem__)


def judge(fact: Any, policy: Any, now: Any) -> list[str]:
    """What the policy makes of one looked-up package, as verdict words."""
    from datetime import datetime, timezone

    verdicts: list[str] = []
    if not fact.checked:
        return verdicts
    licence = license_verdict(fact.license, policy.deny, policy.flag)
    if licence:
        verdicts.append("unknown-license" if licence == "unknown" else licence)
    if fact.published and policy.min_age_days:
        try:
            when = datetime.fromisoformat(fact.published.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if (now - when).days < policy.min_age_days:
                verdicts.append("too-new")
        except ValueError:
            pass
    return verdicts


def facts_for(changes: Sequence[Change], looked: Mapping[tuple[str, str, str], Facts],
              policy: Any, now: Any, written_by: Mapping[str, str] | None = None) -> list[Any]:
    """Each change, with what is known about its new version and the policy's verdict."""
    from .schemas import DependencyFact

    out = []
    for change in changes:
        version = change.after[-1] if change.after else ""
        fact = DependencyFact(
            ecosystem=change.ecosystem, name=change.name, kind=change.kind,
            before=change.before, after=change.after, direct=change.direct, via=change.via,
            lockfile=change.lockfile, private=bool(change.private),
            added_by=(written_by or {}).get(change.lockfile, ""))
        found = looked.get((change.ecosystem, change.name, version))
        if found is not None and change.kind != "removed" and not change.private:
            fact.checked = found.checked
            fact.license, fact.published = found.license, found.published
            fact.advisories, fact.malicious = list(found.advisories), list(found.malicious)
            fact.verdicts = judge(fact, policy, now)
        out.append(fact)
    return out


def findings(facts: Sequence[Any], unlocked: Sequence[tuple[str, str]] = (),
             unreadable: Sequence[str] = ()) -> list[Any]:
    """The policy's verdicts as findings, one per kind of problem.

    Removed packages raise nothing, private ones are never looked up, and a
    package nobody could look up is its own finding -- not checked is not clean.
    """
    from .schemas import Finding

    live = [f for f in facts if f.kind != "removed"]

    def line(f: Any, extra: str = "") -> str:
        version = f.after[-1] if f.after else ""
        how = "direct" if f.direct else (f"through {f.via}" if f.via else "transitive")
        return f"{f.ecosystem} {f.name} {version} ({how})" + (f" — {extra}" if extra else "")

    out = []

    def raise_(fid: str, title: str, severity: str, detail: str, rows: list[str],
               recommendation: str) -> None:
        if rows:
            out.append(Finding(id=fid, title=title, severity=severity, category="dependencies",
                               detail=detail, evidence="\n".join(rows[:60]),
                               recommendation=recommendation,
                               files=sorted({f.lockfile for f in live if f.lockfile})))

    raise_("dependency-malicious", "This feature brings in a package published to do harm",
           "blocker",
           "OSV lists these releases as malicious: a typosquat, a hijacked account, or code "
           "that runs on install. Some may already have run during setup, inside the sealed "
           "environment.",
           [line(f, ", ".join(f.malicious)) for f in live if f.malicious],
           "Remove it, and check what else came in the same change.")
    raise_("dependency-vulnerable", "This feature brings in a version with a known vulnerability",
           "blocker",
           "OSV has advisories against these exact versions. Each id opens at osv.dev.",
           [line(f, ", ".join(f.advisories)) for f in live if f.advisories],
           "Move to a version the advisory says is fixed.")
    raise_("dependency-license", "This feature brings in a package under a license the project does not take",
           "blocker",
           "These licenses are on the project's deny list. A person set that list at gate 0.",
           [line(f, f.license) for f in live if "denied" in f.verdicts],
           "Replace the package, or change the policy if the project can ship under it.")
    raise_("dependency-license-unknown", "This feature brings in packages whose license nobody could read",
           "major",
           "An unknown license is never taken as permissive. The registry gave nothing that "
           "names one.",
           [line(f, f.license or "no license given") for f in live if "unknown-license" in f.verdicts],
           "Find the license in the package's own repository before accepting.")
    raise_("dependency-license-flagged", "This feature brings in packages under a license the project flags",
           "major",
           "Flagged licenses are often fine, depending on how the package is used. A person "
           "decides.",
           [line(f, f.license) for f in live if "flagged" in f.verdicts],
           "Rule on each one.")
    raise_("dependency-too-new", "This feature brings in releases published in the last few days",
           "major",
           "Most hijacked releases are caught and pulled within days of publication. These are "
           "younger than the project's minimum age, so nothing has had time to catch them.",
           [line(f, f"published {f.published[:10]}") for f in live if "too-new" in f.verdicts],
           "Pin the previous release, or wait and look again.")
    raise_("dependency-unchecked", "Some new packages could not be looked up",
           "major",
           "OSV or the package's registry did not answer for these, so nothing is known about "
           "them. That is not the same as nothing being wrong.",
           [line(f) for f in live if not f.checked and not f.private],
           "Look again on the next run; nothing here is a pass.")
    if unlocked:
        out.append(Finding(
            id="dependency-unlocked",
            title="This feature declares packages no lockfile pins",
            severity="major", category="dependencies",
            detail=("These were added to a manifest, but the lockfile beside it was not updated, "
                    "so the version that installs -- and everything it brings -- is decided at "
                    "install time and was never looked at."),
            evidence="\n".join(f"{eco} {name}" for eco, name in unlocked),
            recommendation="Update the lockfile with the project's package manager."))
    if unreadable:
        out.append(Finding(
            id="dependency-lockfile-unreadable",
            title="A lockfile this feature left could not be read",
            severity="major", category="dependencies",
            detail="Nothing in it could be checked. That is not the same as nothing in it being new.",
            evidence="\n".join(unreadable),
            recommendation="Regenerate it with the project's package manager."))
    return out


def _git_text(repo: Any, args: Sequence[str]) -> str | None:
    return git.out(args, repo)


async def project_inventory(repo: Any, ref: str, policy: Any, lookup: Any,
                            previous: Sequence[Any] = ()) -> tuple[list[Any], str]:
    """Every package the project pins at `ref`, looked up, as the survey shows it.

    The starting point a feature's changes are measured from, and the one
    place problems the project already had are shown -- first, and never
    blamed on a feature. An advisory that was not there at the last reading is
    marked `new-advisory`: the package did not change, what is known about it did.
    """
    from datetime import datetime, timezone

    from .schemas import DependencyFact

    listing = _git_text(repo, ["ls-tree", "-r", "--name-only", ref]) or ""
    inv = read_inventory([p for p in listing.splitlines() if p],
                         lambda path: _git_text(repo, ["show", f"{ref}:{path}"]))
    seen: dict[tuple[str, str, str], Locked] = {}
    for pkg in inv.packages:
        seen.setdefault((pkg.ecosystem, pkg.name, pkg.version), pkg)
    keys = [k for k, pkg in seen.items() if not pkg.private]
    looked = await lookup.facts(keys, dates=False) if (policy.enabled and keys) else {}
    known = {(f.ecosystem, f.name): set(f.advisories) | set(f.malicious) for f in previous}
    now = datetime.now(timezone.utc)
    facts = []
    for key, pkg in sorted(seen.items(), key=lambda kv: (not kv[1].direct, kv[0])):
        fact = DependencyFact(ecosystem=pkg.ecosystem, name=pkg.name, after=[pkg.version],
                              direct=pkg.direct, via=pkg.via, lockfile=pkg.lockfile,
                              private=bool(pkg.private))
        found = looked.get(key)
        if found is not None:
            fact.checked = found.checked
            fact.license = found.license
            fact.advisories, fact.malicious = list(found.advisories), list(found.malicious)
            fact.verdicts = [v for v in judge(fact, policy, now) if v != "too-new"]
            was = known.get((pkg.ecosystem, pkg.name))
            if was is not None and (set(fact.advisories) | set(fact.malicious)) - was:
                fact.verdicts.append("new-advisory")
        facts.append(fact)
    notes = []
    if inv.unreadable:
        notes.append("could not read " + ", ".join(inv.unreadable))
    if inv.unlocked:
        notes.append("no lockfile beside " + ", ".join(inv.unlocked)
                     + ", so only the packages it names directly are known")
    if policy.enabled and keys and not any(f.checked for f in facts):
        notes.append("nothing could be looked up")
    return facts, "; ".join(notes)


def inventory_record(facts: Sequence[Any], note: str) -> dict[str, Any]:
    """The ledger entry for one reading of what a project depends on."""
    from datetime import datetime, timezone

    return {"at": datetime.now(timezone.utc).isoformat(),
            "packages": [f.model_dump(mode="json") for f in facts], "note": note}


def latest_inventory(project: Any) -> dict[str, Any] | None:
    """The newest reading on the project's ledger, or None if there never was one."""
    latest = None
    for record in project.store:
        if record.get("kind") == "dependency_inventory":
            latest = record.get("payload")
    return latest


async def refresh_inventory(project: Any, lookup: Any) -> None:
    """Read what the project depends on again, and append it. Never fails its caller:
    a reading that could not happen is itself recorded, as a note."""
    from .schemas import DependencyFact

    try:
        previous = latest_inventory(project) or {}
        before = [DependencyFact.model_validate(p) for p in previous.get("packages") or []]
        facts, note = await project_inventory(
            project.repo_path, project.state.base_ref or "HEAD",
            project.state.dependency_policy, lookup, before)
    except Exception as exc:  # noqa: BLE001 -- a reading never takes its caller down
        facts, note = [], f"could not be read: {type(exc).__name__}: {exc}"
    project.store.append("dependency_inventory", inventory_record(facts, note),
                         role="orchestrator")
