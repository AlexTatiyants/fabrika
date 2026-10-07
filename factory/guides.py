"""Guides: the repository's own files that say how its code is written.

Fabrika is unlikely to be the only thing writing code in a project -- highly
interactive UI work happens in an editor -- so a rule only Fabrika could see
would be a second rulebook. Guides are therefore the files every coding tool
already reads, in the places each convention puts them:

- **AGENTS.md** at the root: how to work here -- commands, code style, API
  conventions, testing. A nested AGENTS.md holds the rules for one folder.
  **CLAUDE.md** is the same thing for Claude Code, usually `@AGENTS.md`.
- **DESIGN.md** at the root, in Google Labs' DESIGN.md format: the design
  tokens in front matter, then how things should look.
- **Skills**, `name/SKILL.md`, in `.claude/skills/` or `.agents/skills/`.
- A document an AGENTS.md points to. One nothing points to does not bind.

What binds is what is committed at the commit a feature starts from. Nothing
here is stored and nothing is approved: the repository says which rules are in
force, and every tool sees the same set.

How they reach an agent depends on whether a harness runs it. OpenHands loads
the root AGENTS.md, CLAUDE.md and the skills itself (see the driver), so a
harness worker is only told where the rules its loader does not read are kept.
A role with no harness -- scout, architect, reviewer, the checkers -- is handed
the same files by code, read at the base commit, under a budget of their own.

No tool is named here: what a guide says is for the agents reading it.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from . import git
from .schemas import Citation, Finding, Guide, ObservedConvention

#: Agent instruction files, looked for in every folder; each covers its own.
AGENT_FILES = ("AGENTS.md", "CLAUDE.md")

#: The design guide, at the root only: the format's own convention.
DESIGN_PATH = "DESIGN.md"
AGENTS_PATH = "AGENTS.md"
CLAUDE_PATH = "CLAUDE.md"

#: Where a project keeps its skills. Fabrika reads all of them; which one it
#: writes to is the project's `skills_dir`.
SKILL_DIRS = (".claude/skills", ".agents/skills", ".openhands/skills")

#: Folders a person may choose for Fabrika to write skills into.
SKILL_DIR_CHOICES = (".claude/skills", ".agents/skills")

#: What a document an AGENTS.md points to may be written in.
TEXT_SUFFIXES = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}

#: Never a project's own guidance, wherever a name turns up.
SKIP_PARTS = {"node_modules", "vendor", "third_party", "dist", "build", ".venv", "venv",
              "site-packages", ".fabrika"}

#: The line that sends every tool from AGENTS.md to DESIGN.md. No tool loads
#: DESIGN.md on its own; each is pointed at it by its instruction file.
DESIGN_LINE = "For anything a person sees, follow DESIGN.md."

LAYER_WORDS: dict[str, str] = {
    "agent": "how to work here",
    "design": "UX and visual style",
    "skill": "a skill",
    "linked": "a document AGENTS.md points to",
}

#: The order guides fill a budget in: what every writer needs first.
LAYER_ORDER: tuple[str, ...] = ("agent", "linked", "design", "skill")

#: Roles handed only the testing sections: the blind test writer and the one
#: that attacks a feature with tests. How the code is styled is not theirs.
TESTING_ONLY = {"oracle", "breaker"}

#: Roles whose guides depend on the files in front of them.
FILE_SCOPED = {"worker", "repairer", "simplifier", "integrator", "reviewer"}

UI_SUFFIXES = {".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html", ".css", ".scss", ".sass", ".less"}

PRECEDENCE = (
    "When two of these disagree, the first wins: a check this project runs (it is a fact), "
    "the repository's guides (its people wrote them), a person's answer about this "
    "feature, an observed convention (a model inferred it), your own preference."
)


# -- reading the repository -----------------------------------------------------


def _git(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    return git.run(args, repo)


def tracked(repo: str | Path, ref: str) -> list[str]:
    """Every file tracked at `ref`, repo-relative."""
    ran = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return [ln for ln in (ran.stdout.splitlines() if ran and ran.returncode == 0 else []) if ln]


def read_at(repo: str | Path, ref: str, path: str) -> str | None:
    """A file as committed at `ref`, or None."""
    ran = _git(repo, "show", f"{ref}:{path}")
    return ran.stdout if ran is not None and ran.returncode == 0 else None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _skipped(path: str) -> bool:
    return any(part in SKIP_PARTS for part in PurePosixPath(path).parts[:-1])


def _skill(path: str) -> bool:
    for folder in SKILL_DIRS:
        if not path.startswith(folder + "/"):
            continue
        rest = path[len(folder) + 1:].split("/")
        # `name/SKILL.md`, the Agent Skills format; and OpenHands' own flat
        # `name.md`, which its loader reads from its own folder.
        if len(rest) == 2 and rest[1] == "SKILL.md":
            return True
        if folder == ".openhands/skills" and len(rest) == 1 and rest[0].endswith(".md"):
            return True
    return False


def detect(files: Sequence[str]) -> list[Guide]:
    """The standard files among `files`: what the conventions say binds."""
    out: list[Guide] = []
    for path in files:
        if _skipped(path):
            continue
        p = PurePosixPath(path)
        if p.name in AGENT_FILES:
            parent = str(p.parent)
            out.append(Guide(path=path, layer="agent", scope="" if parent == "." else parent))
        elif path == DESIGN_PATH:
            out.append(Guide(path=path, layer="design"))
        elif _skill(path):
            out.append(Guide(path=path, layer="skill"))
    return sorted(out, key=_order)


_MD_LINK = re.compile(r"\]\(\s*<?([^)\s>#]+)")
_IMPORT = re.compile(r"(?:^|\s)@([\w./-]+)")
_TICKED = re.compile(r"`([\w./-]+\.[A-Za-z]+)`")


def references(text: str, at: str, present: set[str]) -> list[str]:
    """Files an instruction file points to, that exist: a Markdown link, a
    `@path` import, or a path in backticks. Resolved from the
    file's own folder, then from the root."""
    out: list[str] = []
    for ref in [*_MD_LINK.findall(text), *_IMPORT.findall(text), *_TICKED.findall(text)]:
        if "://" in ref or ref.startswith("mailto:"):
            continue
        ref = ref.strip()
        for candidate in (posixpath.normpath(posixpath.join(at, ref)) if at else None,
                          posixpath.normpath(ref.lstrip("/"))):
            if candidate and candidate in present and candidate not in out:
                out.append(candidate)
                break
    return out


def skill_description(text: str) -> str:
    """A skill's own account of when it applies, from its front matter."""
    match = re.match(r"^---\s*\n(.*?)\n---", text or "", re.S)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "description":
            return value.strip().strip("\"'")[:300]
    return ""


def found(repo: str | Path, ref: str, files: Sequence[str] | None = None) -> list[Guide]:
    """The guides committed at `ref`, each with its hash, as they bind there.

    The standard files, and the documents an instruction file points to. A
    document nothing points to is not here: it does not bind, however much it
    reads like a guide.
    """
    files = list(files) if files is not None else tracked(repo, ref)
    present = set(files)
    guides = detect(files)
    standard = {g.path for g in guides}
    linked: dict[str, Guide] = {}
    for guide in guides:
        text = read_at(repo, ref, guide.path)
        if text is None:
            continue
        guide.sha256 = digest(text)
        if guide.layer == "skill":
            guide.description = skill_description(text)
        if guide.layer != "agent":
            continue
        for path in references(text, guide.scope, present):
            if (path in standard or path in linked or _skipped(path)
                    or PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES):
                continue
            body = read_at(repo, ref, path)
            if body is None:
                continue
            linked[path] = Guide(path=path, layer="linked", scope=guide.scope,
                                 sha256=digest(body), referenced_by=guide.path)
    return sorted([*guides, *linked.values()], key=_order)


def _order(g: Guide) -> tuple[int, int, str]:
    return (LAYER_ORDER.index(g.layer), g.scope.count("/") + (1 if g.scope else 0), g.path)


# -- which guides a role is handed ----------------------------------------------


def applies(guide: Guide, files: Sequence[str] | None) -> bool:
    """Whether a guide covers any of `files`; every guide covers a feature as a whole."""
    if files is None or not guide.scope:
        return True
    prefix = guide.scope.rstrip("/") + "/"
    return any(str(f).startswith(prefix) for f in files)


def touches_ui(files: Sequence[str]) -> bool:
    return any(PurePosixPath(f).suffix.lower() in UI_SUFFIXES for f in files)


def _only_imports(text: str) -> bool:
    """A CLAUDE.md that only imports AGENTS.md says nothing of its own."""
    rest = [ln for ln in text.splitlines()
            if ln.strip() and not re.fullmatch(r"\s*@[\w./-]+\s*", ln)]
    return not rest


def select(guides: Sequence[Guide], role: str, files: Sequence[str] | None = None
           ) -> list[tuple[Guide, str]]:
    """What one role with no harness is handed, and how: `whole`, `testing`
    (its testing sections only) or `index` (a skill by name and description).

    Instruction files that cover the files in question; DESIGN.md for work and
    reviews that touch what a person sees; skills by what they are for. The
    blind test writer gets the testing sections and nothing else.
    """
    scoped = role in FILE_SCOPED and files is not None
    out: list[tuple[Guide, str]] = []
    for guide in guides:
        if not applies(guide, files if scoped else None):
            continue
        if role in TESTING_ONLY:
            if guide.layer == "agent":
                out.append((guide, "testing"))
            continue
        if guide.layer == "design":
            if scoped and not touches_ui(files or []):
                continue
            out.append((guide, "whole"))
        elif guide.layer == "skill":
            out.append((guide, "index"))
        else:
            out.append((guide, "whole"))
    return out


# -- what a role is handed, as text ---------------------------------------------


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _sections(text: str) -> list[tuple[int, str, int, int]]:
    """Each heading's level, title, and the line span it governs."""
    lines = text.splitlines()
    heads: list[tuple[int, str, int]] = []
    fenced = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if m:
            heads.append((len(m.group(1)), m.group(2), i))
    out = []
    for n, (level, title, start) in enumerate(heads):
        end = next((s for lv, _, s in heads[n + 1:] if lv <= level), len(lines))
        out.append((level, title, start, end))
    return out


def testing_sections(text: str) -> str:
    """The sections of an instruction file about tests, whole, with what is under them."""
    lines = text.splitlines()
    taken: list[tuple[int, int]] = []
    for level, title, start, end in _sections(text):
        if not re.search(r"\btest", title, re.I):
            continue
        if any(s <= start < e for s, e in taken):
            continue
        # A heading with nothing under it says nothing about tests.
        if not any(ln.strip() and not _HEADING.match(ln) for ln in lines[start + 1:end]):
            continue
        taken.append((start, end))
    return "\n\n".join("\n".join(lines[s:e]).strip() for s, e in taken)


def _headings(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#")) or "(no headings)"


def render(repo: str | Path, ref: str, chosen: Sequence[tuple[Guide, str]],
           budget: int) -> tuple[str, list[dict[str, Any]]]:
    """The guides' text as they stood at `ref`, within `budget`, and what was handed.

    A guide that does not fit is handed as its headings and its path, and said
    to be partial -- never presented as whole. A document the testing sections
    point to is handed with them: it is what those sections say.
    """
    blocks: list[str] = []
    delivered: list[dict[str, Any]] = []
    left = max(0, budget)
    files = None
    queue = list(chosen)
    handed: set[str] = set()
    while queue:
        guide, how = queue.pop(0)
        if guide.path in handed:
            continue
        handed.add(guide.path)
        if how == "index":
            blocks.append(f"### `{guide.path}` -- {LAYER_WORDS['skill']}: "
                          + (guide.description or "no description; open it to see what it is for"))
            delivered.append({"path": guide.path, "sha256": guide.sha256, "partial": False,
                              "part": "name and description"})
            continue
        text = read_at(repo, ref, guide.path)
        if text is None:
            continue
        if guide.path.endswith(CLAUDE_PATH) and _only_imports(text):
            continue
        body = text.strip()
        part = ""
        if how == "testing":
            body = testing_sections(text)
            if not body:
                continue
            part = "testing sections"
            if files is None:
                files = set(tracked(repo, ref))
            for path in references(body, guide.scope, files):
                if PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES:
                    queue.append((Guide(path=path, layer="linked", scope=guide.scope,
                                        referenced_by=guide.path), "whole"))
        if guide.layer == "linked":
            where = f"pointed to by `{guide.referenced_by}`" if guide.referenced_by else "pointed to by AGENTS.md"
        else:
            where = f"applies under `{guide.scope}/`" if guide.scope else "applies to the whole repository"
        head = f"### `{guide.path}` -- {LAYER_WORDS[guide.layer]}, {where}"
        if part:
            head += f" -- its {part} only"
        whole = len(body) <= left
        if not whole:
            body = _headings(body)
            head += (" -- PARTIAL: only its headings fit here. Open the file for the rest; "
                     "if you cannot, say so rather than guess what it says")
        left = max(0, left - len(body))
        blocks.append(f"{head}\n\n{body}")
        delivered.append({"path": guide.path, "sha256": digest(text), "partial": not whole,
                          "part": part})
    return "\n\n".join(blocks), delivered


def section(guide_text: str, observed: Sequence[ObservedConvention] = (),
            do_not_duplicate: Sequence[str] = (), pointers: str = "") -> str:
    """"How this repository is written": guides first, then what was inferred."""
    if not guide_text and not observed and not do_not_duplicate and not pointers:
        return ""
    parts = ["# How this repository is written", "", PRECEDENCE]
    if pointers:
        parts += ["", pointers]
    if guide_text:
        parts += ["", "## Guides -- authoritative, from the repository at the commit this "
                      "feature starts from", "", guide_text]
    if observed:
        parts += ["", "## Observed conventions -- inferred from the code, where no guide speaks", ""]
        parts += [f"- {c.rule} (`{c.slug}`)" + (f" -- seen in {', '.join(c.files[:4])}" if c.files else "")
                  for c in observed]
    if do_not_duplicate:
        parts += ["", "## Already in this repository -- use it, do not rebuild it", ""]
        parts += [f"- {item}" for item in do_not_duplicate]
    return "\n".join(parts)


# -- what a harness sees, and the bridge to the rest ----------------------------
#
# Each coding tool reads its own files: one CLAUDE.md and `.claude/skills/`,
# another AGENTS.md and `.agents/skills/`. A project that began with one tool
# keeps its rules where that tool wanted them, and Fabrika may start a worker
# on another -- by configuration, or because a subscription ran out. So before
# a session, the rules that runner would not read are put where it reads them,
# in the checkout only: a skill linked into its folder, an instruction file
# written in the name it looks for. Hidden from the unit's changes and removed
# when the session ends; nothing harness-specific is ever committed. What
# cannot be bridged -- DESIGN.md, a folder's rules for a runner that reads only
# the root's, a document AGENTS.md points to -- is named in the task instead.


class Reads:
    """What one runner reads of a repository's rules on its own, from its route."""

    def __init__(self, instructions: Sequence[str] = (), nested: bool = False,
                 skills: Sequence[str] = ()) -> None:
        self.instructions = list(instructions)
        self.nested = bool(nested)
        self.skills = list(skills)

    @classmethod
    def of(cls, route: Any) -> "Reads":
        if route is None:
            return cls()
        return cls(getattr(route, "reads_instructions", []) or [],
                   getattr(route, "reads_nested", False),
                   getattr(route, "reads_skills", []) or [])


class Bridge:
    """What was put into a checkout for one session, so it can be taken out."""

    def __init__(self) -> None:
        self.made: list[str] = []          # repo-relative paths created
        self.dirs: list[str] = []          # folders created, deepest last
        self.written: dict[str, str] = {}  # generated file -> its contents
        self.skills: list[str] = []        # skills linked in, by their real path
        self.instructions: list[tuple[str, str]] = []  # (written, from)


def _tracked(root: Path) -> list[str]:
    ran = _git(root, "ls-files")
    return [ln for ln in (ran.stdout.splitlines() if ran and ran.returncode == 0 else []) if ln]


def _inline(root: Path, rel: str, depth: int = 0) -> str:
    """An instruction file's text with its `@path` imports read in, so a tool
    that does not follow them still gets what they say."""
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    if depth > 1:
        return text
    here = posixpath.dirname(rel)
    out = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s*@([\w./-]+)\s*", line)
        target = posixpath.normpath(posixpath.join(here, m.group(1))) if m else ""
        if m and (root / target).is_file():
            out.append(_inline(root, target, depth + 1).rstrip())
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def bridge(root: str | Path, reads: Reads) -> Bridge:
    """Put the rules this runner would not read where it reads them, in this
    checkout only. Nothing that exists is touched: a skill of the same name in
    the runner's own folder wins, and a folder that has its own instruction
    file keeps it."""
    root = Path(root)
    out = Bridge()
    files = _tracked(root)

    # Instruction files. A folder with rules in a file this runner does not
    # read gets one it does, saying the same; only the root's, for a runner
    # that does not read a folder's own.
    wanted = [n for n in reads.instructions if n]
    if wanted:
        folders: dict[str, set[str]] = {}
        for path in files:
            p = PurePosixPath(path)
            if p.name in (*AGENT_FILES, *wanted) and not _skipped(path):
                folders.setdefault(str(p.parent), set()).add(p.name)
        for folder, names in sorted(folders.items()):
            if folder != "." and not reads.nested:
                continue
            if names & set(wanted):
                continue
            source = next((n for n in AGENT_FILES if n in names), None)
            if source is None:
                continue
            src = source if folder == "." else f"{folder}/{source}"
            dst = wanted[0] if folder == "." else f"{folder}/{wanted[0]}"
            if (root / dst).exists():
                continue
            text = (f"<!-- Written by Fabrika for this session only, from {source}. -->\n"
                    + _inline(root, src))
            (root / dst).write_text(text, encoding="utf-8")
            out.made.append(dst)
            out.written[dst] = text
            out.instructions.append((dst, src))

    # Skills. Each one in a folder this runner does not read is linked into
    # the first folder it does.
    if reads.skills:
        target = reads.skills[0]
        target_dir = root / target
        for folder in SKILL_DIRS:
            if folder in reads.skills or folder == ".openhands/skills":
                continue
            source_dir = root / folder
            if not source_dir.is_dir():
                continue
            if target_dir.exists() and target_dir.resolve() == source_dir.resolve():
                continue  # one folder linked to the other: already the same skills
            for skill in sorted(source_dir.iterdir()):
                if not (skill / "SKILL.md").is_file():
                    continue
                link = target_dir / skill.name
                if link.exists() or link.is_symlink():
                    continue
                for parent in reversed([*link.parents][: len(Path(target).parts)]):
                    if not parent.exists():
                        parent.mkdir()
                        out.dirs.append(str(parent.relative_to(root)))
                link.symlink_to(Path(*[".."] * len(Path(target).parts)) / folder / skill.name,
                                target_is_directory=True)
                out.made.append(str(link.relative_to(root)))
                out.skills.append(f"{folder}/{skill.name}/SKILL.md")
    return out


def unbridge(root: str | Path, done: Bridge) -> None:
    """Take out what `bridge` put in. A generated file the session changed is
    left where it is: that is the session's work, and is seen as such."""
    root = Path(root)
    for rel in done.made:
        path = root / rel
        if path.is_symlink():
            path.unlink()
        elif path.is_file() and path.read_text(encoding="utf-8", errors="replace") == done.written.get(rel):
            path.unlink()
    for rel in reversed(done.dirs):
        try:
            (root / rel).rmdir()
        except OSError:
            pass  # something else is in it now


def _loaded(guide: Guide, reads: Reads, done: Bridge) -> bool:
    if guide.layer == "agent":
        name = PurePosixPath(guide.path).name
        bridged = {src for _, src in done.instructions}
        if guide.scope and not reads.nested:
            return False
        return name in reads.instructions or guide.path in bridged
    if guide.layer == "skill":
        return (any(guide.path.startswith(f + "/") for f in reads.skills)
                or guide.path in done.skills)
    return False


def harness_note(guides: Sequence[Guide], files: Sequence[str], reads: Reads, done: Bridge) -> str:
    """What one runner was given of the repository's rules, and where the rest is.

    Said for the runner that actually started, after the bridge: loaded is
    only what it reads itself or was bridged to. Everything else that covers
    the unit's files is named, not pasted -- the files are on disk.
    """
    covering = [g for g in guides if applies(g, files)
                and not (g.layer == "design" and not touches_ui(files))]
    loaded = [g for g in covering if _loaded(g, reads, done)]
    rest = [g for g in covering if g not in loaded]
    if not covering:
        return ""
    parts = ["## This repository's rules, as your harness has them"]
    if loaded:
        rules = [g.path for g in loaded if g.layer == "agent"]
        skills = [g for g in loaded if g.layer == "skill"]
        said = ", ".join(f"`{p}`" for p in rules)
        if skills:
            said += (", and " if said else "") + f"{len(skills)} skill{'s' if len(skills) != 1 else ''}"
        parts += ["", f"Loaded for you: {said}. Follow them; do not edit them."]
    if rest:
        lines = []
        for g in rest:
            if g.layer == "agent":
                lines.append(f"- `{g.path}` -- "
                             + (f"the rules for `{g.scope}/`" if g.scope else "how to work in this repository"))
            elif g.layer == "design":
                lines.append(f"- `{g.path}` -- how anything a person sees should look and behave")
            elif g.layer == "linked":
                lines.append(f"- `{g.path}` -- pointed to by `{g.referenced_by}`")
            else:
                lines.append(f"- `{g.path}` -- a skill: {g.description or 'open it to see what it is for'}")
        parts += ["", "Your harness does not load these. Open each that applies before you change "
                      "anything, and do not edit them:", "", *lines]
    return "\n".join(parts)


def skills_home(files: Sequence[str], chosen: str = "") -> tuple[str, str]:
    """Where a new skill goes, and why: a person's choice, else where this
    project's skills already are, else the open standard's folder -- unless
    the people here use a tool that reads only its own."""
    if chosen:
        return chosen, "chosen for this project"
    count = {d: sum(1 for f in files if _skill(f) and f.startswith(d + "/")) for d in SKILL_DIR_CHOICES}
    if any(count.values()):
        folder = max(SKILL_DIR_CHOICES, key=lambda d: count[d])
        return folder, f"where this project's {_n(count[folder], 'skill')} already {'is' if count[folder] == 1 else 'are'}"
    if any(f.startswith(".claude/") for f in files):
        return ".claude/skills", "no skills yet, and this project has a .claude/ folder, so its people use a tool that reads only that one"
    return ".agents/skills", "no skills yet; the open standard's folder, which other runners are bridged to"


def skill_clashes(guides: Sequence[Guide]) -> list[tuple[str, list[str]]]:
    """Skills of the same name in two folders: each runner sees only its own."""
    by: dict[str, list[str]] = {}
    for g in guides:
        if g.layer == "skill" and g.path.endswith("/SKILL.md"):
            by.setdefault(PurePosixPath(g.path).parent.name, []).append(g.path)
    return sorted((name, paths) for name, paths in by.items() if len(paths) > 1)


def index(guides: Sequence[Guide]) -> str:
    """The guides by path, for a report that cites them."""
    if not guides:
        return ""
    return "# This repository's guides\n\n" + "\n".join(
        f"- `{g.path}` -- {LAYER_WORDS[g.layer]}"
        + (f", under `{g.scope}/`" if g.scope and g.layer == "agent" else "") for g in guides)


# -- citations ------------------------------------------------------------------

#: Findings about how code is written. A rule must be named for one of these to
#: carry weight; correctness and security stand on their own evidence.
STYLE_CATEGORIES = {"convention", "naming", "style", "layout", "unearned abstraction"}

_SEVERITY_RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*_>\"'“”‘’]", "", text or "")).strip().lower()


def _capped(finding: Finding, why: str) -> Finding:
    if _SEVERITY_RANK.get(finding.severity, 0) > _SEVERITY_RANK["minor"]:
        finding.severity = "minor"  # type: ignore[assignment]
        finding.detail = f"{finding.detail}\n\n(Capped at minor: {why}.)"
    return finding


def verify_citations(findings: Sequence[Finding], read: Any, binding: set[str],
                     observed: set[str]) -> list[Finding]:
    """Hold each finding's citation to what it cites, by code.

    A guide quote must appear in that guide as it stood at the base commit --
    words compared, not punctuation. One that does not is marked unverified
    and capped at minor: a rule that does not exist is a fabricated objection,
    worse than none. An observed convention is real if the scout named it, and
    capped at minor anyway, because a model inferred it. A style finding that
    cites nothing is capped too; correctness and security need no rule.
    """
    out: list[Finding] = []
    for original in findings:
        f = original.model_copy(deep=True)
        cite = f.cites or Citation()
        if cite.source == "guide":
            text = read(cite.ref) if cite.ref in binding else None
            f.cite_verified = bool(text is not None and _norm(cite.quote)
                                   and _norm(cite.quote) in _norm(text))
            if not f.cite_verified:
                f = _capped(f, f"the rule it cites is not in {cite.ref or 'any guide in the repository'}")
        elif cite.source == "observed":
            f.cite_verified = cite.ref in observed
            f = _capped(f, "it rests on a convention a model inferred, not a written rule")
        elif (f.category or "").strip().lower() in STYLE_CATEGORIES:
            f.cite_verified = None
            f = _capped(f, "it is about style and cites no written rule")
        out.append(f)
    return out


def edited(changed: Iterable[str]) -> list[str]:
    """The guide files among `changed`: an instruction file, DESIGN.md, a skill.

    Read from paths, not from what binds at base: a feature that adds a skill
    has changed the rules as surely as one that edits one."""
    out = []
    for path in changed:
        p = PurePosixPath(path)
        if p.name in AGENT_FILES or path == DESIGN_PATH or any(
                path == folder or path.startswith(folder + "/") for folder in SKILL_DIRS):
            out.append(path)
    return sorted(set(out))


#: The guide files as protected-set globs: repair and simplify may not write them.
PROTECTED_GLOBS = ("AGENTS.md", "**/AGENTS.md", "CLAUDE.md", "**/CLAUDE.md", DESIGN_PATH,
                   *(f"{folder}/**" for folder in SKILL_DIRS))


# -- what Fabrika may propose (phase 5) -----------------------------------------


def has_ui(files: Sequence[str]) -> bool:
    """Whether the repository has screens a person looks at."""
    return any(PurePosixPath(f).suffix.lower() in {".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html"}
               and not _skipped(f) for f in files)


_CSS_VAR = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+);")


def design_tokens(read: Any, files: Sequence[str], limit: int = 400) -> list[tuple[str, str, str]]:
    """Design tokens as the code defines them: CSS custom properties, and the
    values in a tokens JSON file. Names and values only -- no rules."""
    out: list[tuple[str, str, str]] = []
    for path in files:
        if _skipped(path):
            continue
        suffix = PurePosixPath(path).suffix.lower()
        name = PurePosixPath(path).name.lower()
        if suffix in (".css", ".scss"):
            for var, value in _CSS_VAR.findall(read(path) or ""):
                out.append((path, var, " ".join(value.split())))
        elif suffix == ".json" and "token" in name:
            try:
                data = json.loads(read(path) or "")
            except ValueError:
                continue

            def walk(node: Any, prefix: str) -> None:
                if isinstance(node, dict):
                    if "value" in node and not isinstance(node["value"], (dict, list)):
                        out.append((path, prefix, str(node["value"])))
                        return
                    for key, val in node.items():
                        walk(val, f"{prefix}.{key}" if prefix else str(key))
                elif not isinstance(node, list):
                    out.append((path, prefix, str(node)))
            walk(data, "")
        if len(out) >= limit:
            break
    seen: dict[str, tuple[str, str, str]] = {}
    for item in out:
        # The first definition wins: `:root` comes before a dark-mode override.
        seen.setdefault(item[1], item)
    return list(seen.values())[:limit]


_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|(rgba?|hsla?|hwb|oklch|oklab|lch|lab|color-mix)\(.*\))$")
_DIMENSION = re.compile(r"^-?\d*\.?\d+(px|rem|em)$")
_VAR_REF = re.compile(r"^var\(\s*(--[A-Za-z0-9_-]+)\s*\)$")


def _token_key(name: str, *prefixes: str) -> str:
    key = name.lstrip("-").replace(".", "-")
    for prefix in prefixes:
        if key.startswith(prefix + "-") and len(key) > len(prefix) + 1:
            return key[len(prefix) + 1:]
    return key


def _yaml_scalar(value: str) -> str:
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    return json.dumps(value)


def design_front_matter(name: str, tokens: Sequence[tuple[str, str, str]]) -> dict[str, Any]:
    """DESIGN.md's front matter, from the tokens the code defines.

    Sorted into the format's groups by what each value is: a colour is a
    colour, a radius a rounding, a gap a spacing. A token that refers to
    another is written as the format's reference. A value the format cannot
    hold -- a calc(), a percentage -- is left out rather than bent to fit.
    """
    groups: dict[str, dict[str, Any]] = {"colors": {}, "typography": {}, "rounded": {}, "spacing": {}}
    where: dict[str, tuple[str, str]] = {}
    pending: list[tuple[str, str]] = []
    for _path, var, value in tokens:
        lower = var.lower()
        ref = _VAR_REF.match(value)
        if ref:
            pending.append((var, ref.group(1)))
            continue
        if _COLOR.match(value):
            key = _token_key(var, "color", "colors", "c")
            groups["colors"][key] = value
            where[var] = ("colors", key)
        elif _DIMENSION.match(value) and re.search(r"radius|rounded|round", lower):
            key = _token_key(var, "radius", "rounded", "border-radius")
            groups["rounded"][key] = value
            where[var] = ("rounded", key)
        elif _DIMENSION.match(value) and re.search(r"space|spacing|gap|gutter|pad|margin", lower):
            key = _token_key(var, "space", "spacing", "gap")
            groups["spacing"][key] = value
            where[var] = ("spacing", key)
        elif re.search(r"font-family|font-sans|font-serif|font-mono|^--font$", lower) \
                or (lower.startswith("--font") and "," in value):
            key = _token_key(var, "font-family", "font")
            groups["typography"].setdefault(key, {})["fontFamily"] = value.split(",")[0].strip().strip("\"'")
        elif _DIMENSION.match(value) and re.search(r"font-size|text-|fs-", lower):
            key = _token_key(var, "font-size", "text", "fs")
            groups["typography"].setdefault(key, {})["fontSize"] = value
    for var, target in pending:
        if target in where:
            group, key = where[target]
            own = _token_key(var, {"colors": "color", "rounded": "radius", "spacing": "space"}[group])
            groups[group].setdefault(own, "{" + f"{group}.{key}" + "}")
    front: dict[str, Any] = {"version": "alpha", "name": name}
    front.update({k: v for k, v in groups.items() if v})
    return front


def _yaml(node: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    out: list[str] = []
    for key, value in node.items():
        # A key YAML would read as anything but a string -- `4`, `true` -- is quoted.
        key = key if re.fullmatch(r"[A-Za-z_][\w-]*", key) and key.lower() not in (
            "true", "false", "null", "yes", "no", "on", "off") else json.dumps(key)
        if isinstance(value, dict):
            out.append(f"{pad}{key}:")
            out += _yaml(value, indent + 1)
        else:
            out.append(f"{pad}{key}: {_yaml_scalar(str(value))}")
    return out


DESIGN_SECTIONS = ("Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes",
                   "Components", "Do's and Don'ts")


def design_md(name: str, tokens: Sequence[tuple[str, str, str]]) -> str:
    """A DESIGN.md in the format's shape: tokens from code, sections for a person.

    Fabrika states no design rule nobody wrote. The front matter is what the
    code already defines; every section is headed and left for this project's
    people to fill.
    """
    sources = sorted({path for path, _, _ in tokens})
    lines = ["---", *_yaml(design_front_matter(name, tokens)), "---", "",
             f"<!-- The tokens above were read from {', '.join(f'`{s}`' for s in sources[:6])}"
             f"{' and more' if len(sources) > 6 else ''}. Each section below is for this "
             "project's people to write: how these are used, and what to avoid. -->", ""]
    for heading in DESIGN_SECTIONS:
        lines += [f"## {heading}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _as_run_by_hand(command: str) -> str:
    """A check's command as a person would type it: without the arguments
    that only tell the tool where to leave a report for Fabrika."""
    kept = [t for t in command.split(" ") if not re.search(r"\{(report|paths)\}", t)]
    return " ".join(kept).strip()


def agents_md(checks: Sequence[Any], has_design: bool, drafted: str = "") -> str:
    """An AGENTS.md: commands from the checks this project runs, which are
    facts; code style and testing sections drafted from what features observed,
    or headed and left for a person when nothing has been observed yet."""
    lines = ["# AGENTS.md", "", "## Commands", ""]
    lines += [f"- {g.name}: `{_as_run_by_hand(g.command)}`" for g in checks
              if getattr(g, "command", "")] or ["<!-- None yet. -->"]
    if has_design:
        lines += ["", "## Design", "", DESIGN_LINE]
    if drafted.strip():
        lines += ["", drafted.strip()]
    else:
        lines += ["", "## Code style", "", "## Testing"]
    return "\n".join(lines).rstrip() + "\n"


def claude_md() -> str:
    """CLAUDE.md for Claude Code, which reads it and not AGENTS.md: one import."""
    return "@AGENTS.md\n"


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def mentions_design(text: str) -> bool:
    return bool(re.search(r"\bDESIGN\.md\b", text or ""))


def working_guides(repo: str | Path) -> list[dict[str, Any]]:
    """Guide files in the working copy that its last commit does not have as
    they are: new or edited instruction files, DESIGN.md, skills -- and the
    documents those instruction files point to, likewise uncommitted. Each
    with its text as it stands and that text's hash, so a commit of it can
    tell whether it changed after a person looked."""
    root = Path(repo).expanduser()
    ran = _git(root, "status", "--porcelain", "--untracked-files=all")
    changed: list[str] = []
    for line in (ran.stdout.splitlines() if ran and ran.returncode == 0 else []):
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.append(path.strip('"'))
    paths = [p for p in edited(changed) if (root / p).is_file() and not _skipped(p)]
    present = set(changed) | set(tracked(root, "HEAD"))
    for path in list(paths):
        if PurePosixPath(path).name not in AGENT_FILES:
            continue
        here = str(PurePosixPath(path).parent)
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for ref in references(text, "" if here == "." else here, present):
            if (ref in changed and ref not in paths and (root / ref).is_file()
                    and PurePosixPath(ref).suffix.lower() in TEXT_SUFFIXES):
                paths.append(ref)
    out = []
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        out.append({"path": path, "contents": text, "was": digest(text)})
    return out


def offers(files: Sequence[str], declined: Sequence[str], *, tokens: Sequence[tuple[str, str, str]],
           checks: Sequence[Any], agents_text: str | None, name: str,
           draft: Any = None, working: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """What Fabrika may propose writing, each only where its evidence is.

    Every proposal is a commit a person edits and approves; nothing is written
    without a press, and a declined one is not offered again. `writes` is what
    the press commits: a new file, or an addition to one.

    Guide files already in the working copy and not yet committed come first,
    as one proposal to commit them as they are -- and nothing is proposed for a
    path one of them holds: offering to create a file that is sitting there
    uncommitted would only be refused.
    """
    out = _offers(files, declined, tokens=tokens, checks=checks, agents_text=agents_text,
                  name=name, draft=draft)
    if not working:
        return out
    held = {w["path"] for w in working}
    out = [o for o in out if not any(w["path"] in held for w in o.get("writes") or [])
           and not (o["kind"] == "ux_gap" and DESIGN_PATH in held)]
    key = "commit_working:" + digest("|".join(f"{w['path']}={w['was']}" for w in working))[:12]
    if key in declined:
        return out
    return [{"kind": "commit_working", "key": key, "layer": "working", "path": "",
             "title": f"Commit the {_n(len(working), 'guide file')} in your working copy",
             "why": "These are in your working copy and not committed, so no agent follows them yet "
                    "-- features start from what is committed. Review them and commit them as "
                    "they are, or as you change them here.",
             "writes": [{"path": w["path"], "contents": w["contents"], "was": w["was"],
                         "as_is": True, "append": False} for w in working]}, *out]


def _offers(files: Sequence[str], declined: Sequence[str], *, tokens: Sequence[tuple[str, str, str]],
            checks: Sequence[Any], agents_text: str | None, name: str,
            draft: Any = None) -> list[dict[str, Any]]:
    present = set(files)
    has_agents = agents_text is not None
    has_design = DESIGN_PATH in present
    out: list[dict[str, Any]] = []
    if has_ui(files) and not has_design:
        if tokens and "design_md" not in declined:
            writes = [{"path": DESIGN_PATH, "contents": design_md(name, tokens), "append": False}]
            if has_agents and not mentions_design(agents_text or ""):
                writes.append({"path": AGENTS_PATH, "contents": f"## Design\n\n{DESIGN_LINE}",
                               "append": True})
            out.append({
                "kind": "design_md", "layer": "design", "path": DESIGN_PATH, "writes": writes,
                "title": f"Start DESIGN.md from the {_n(len(tokens), 'design token')} the code defines",
                "why": f"This project has screens and no DESIGN.md. Its code defines {len(tokens)} "
                       "design tokens; this puts them in DESIGN.md's front matter and heads its "
                       "sections for you to fill. It states no rule of its own."
                       + (" AGENTS.md gets one line pointing to it." if len(writes) > 1 else "")})
        elif not tokens:
            out.append({"kind": "ux_gap", "layer": "design", "path": "", "writes": [],
                        "title": "Screens, and no DESIGN.md to say how they should look",
                        "why": "This project has screens and no DESIGN.md, and its code defines no "
                               "design tokens to start one from. A DESIGN.md of your own would be "
                               "read by every tool that writes code here."})
    if has_design and has_agents and not mentions_design(agents_text or "") \
            and "design_line" not in declined:
        out.append({"kind": "design_line", "layer": "design", "path": AGENTS_PATH,
                    "title": "Point AGENTS.md at DESIGN.md",
                    "writes": [{"path": AGENTS_PATH, "contents": f"## Design\n\n{DESIGN_LINE}",
                                "append": True}],
                    "why": "No tool reads DESIGN.md on its own, and AGENTS.md does not point to it, "
                           "so nothing that writes code here is sent there."})
    if not has_agents and "agents_md" not in declined:
        drafted = draft.markdown if draft is not None else ""
        commands = sum(1 for g in checks if getattr(g, "command", ""))
        out.append({"kind": "agents_md", "layer": "agent", "path": AGENTS_PATH,
                    "title": f"Add AGENTS.md with the commands of this project's {_n(commands, 'check')}"
                    + (f" and {_n(len(draft.rules), 'rule')} features observed" if draft is not None else ""),
                    "draft": draft.id if draft is not None else "",
                    "writes": [{"path": AGENTS_PATH, "append": False,
                                "contents": agents_md(checks, has_design, drafted)}],
                    "why": "This project has no AGENTS.md, the file every coding agent reads first. "
                           "Its commands are the checks this project runs"
                           + ("; its code style and testing sections are drafted from what "
                              f"{draft.features} features observed." if draft is not None else
                              "; its code style and testing sections are left for you.")})
    claude_used = any(f.startswith(".claude/") for f in present)
    if claude_used and CLAUDE_PATH not in present and (has_agents or "agents_md" not in declined) \
            and "claude_md" not in declined:
        out.append({"kind": "claude_md", "layer": "agent", "path": CLAUDE_PATH,
                    "title": "Point Claude Code at AGENTS.md with a one-line CLAUDE.md",
                    "writes": [{"path": CLAUDE_PATH, "contents": claude_md(), "append": False}],
                    "why": "This project has a .claude/ folder, so its people use Claude Code, which "
                           "reads CLAUDE.md first and, before version 2.1.277 or with its fallback "
                           "switched off, never AGENTS.md. One line importing AGENTS.md gives it the "
                           "same rules. Fabrika's own workers do not need it: they are bridged."})
    # Skills kept for one tool, in a project whose people also use another by
    # hand. Fabrika bridges its own sessions; a person's editor it cannot, so
    # one committed link is offered -- only on evidence of the second tool.
    def has(prefix: str) -> bool:
        return any(f == prefix or f.startswith(prefix + "/") for f in present)
    for kind, folder, other, evidence, tool in (
            ("skills_link_agents", ".agents/skills", ".claude/skills", ".codex", "Codex"),
            ("skills_link_claude", ".claude/skills", ".agents/skills", ".claude", "Claude Code")):
        if has(folder) or not any(_skill(f) and f.startswith(other + "/") for f in present):
            continue
        if not has(evidence) or kind in declined:
            continue
        out.append({"kind": kind, "layer": "skill", "path": folder,
                    "title": f"Link {folder}/ to {other}/, so {tool} run by hand sees the skills",
                    "writes": [{"path": folder, "link": f"../{other}", "append": False, "contents": ""}],
                    "why": f"This project's skills are in {other}/, and it has a {evidence}/ folder, so "
                           f"its people use {tool}, which reads only {folder}/. Fabrika bridges its own "
                           f"sessions without committing anything; this one link does the same for "
                           f"everyone else. The skills stay where they are."})
    return out


#: The check DESIGN.md's own linter makes, offered once a DESIGN.md exists.
DESIGN_LINT_COMMAND = "npx --yes @google/design.md lint DESIGN.md"
