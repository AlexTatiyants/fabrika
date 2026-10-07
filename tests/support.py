"""Reading the codebase's own source, for the tests that read it as text.

A good share of the suite asserts on source: a slice of the console between
two function names, a rule in the stylesheet, a line in a method. Those tests
used to open the file they meant directly, which tied every one of them to
the file it happened to live in -- and made splitting a large file a change to
hundreds of tests that had nothing to do with it.

They read through these instead. Each answers with the whole of what it names
in the order it was written, however many files that is on disk, so a slice
between two markers still finds both.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_DIR = ROOT / "console"
FACTORY_DIR = ROOT / "factory"

#: The console's own scripts, in the order index.html loads them: one script
#: split by area, sharing one global scope. ui/ is separate: those are
#: modules, read by the tests that name them.
CONSOLE_SCRIPTS = [
    "app/vocab.js",
    "app/state.js",
    "app/header.js",
    "app/steps.js",
    "app/util.js",
    "app/rail.js",
    "app/agents.js",
    "app/routes.js",
    "app/agent-config.js",
    "app/control-room.js",
    "app/setup.js",
    "app/readings.js",
    "app/yard.js",
    "app/project.js",
    "app/project-questions.js",
    "app/guides.js",
    "app/history.js",
    "app/help.js",
    "app/project-screen.js",
    "app/intake.js",
    "app/running.js",
    "app/ledger.js",
    "app/review.js",
    "app/render.js",
    "app/data.js",
    "app/interaction.js",
    "app/sheet.js",
    "app/actions.js",
    "app/boot.js",
]
#: The stylesheets, in the order index.html links them.
STYLESHEETS = [
    "css/tokens.css",
    "css/base.css",
    "css/screens.css",
    "css/crew.css",
    "css/agent-config.css",
    "css/folds-toast.css",
    "css/gate0.css",
    "css/rework.css",
    "css/gate2.css",
    "css/gate1.css",
    "css/start.css",
    "css/reskin-board.css",
    "css/reskin-shell.css",
    "css/gate0-steps.css",
    "css/review.css",
    "css/yard.css",
    "css/runs-crew.css",
    "css/project.css",
    "css/asbuilt.css",
    "css/setup.css",
]


def app_js() -> str:
    """The console's script, as one text."""
    return "\n".join((CONSOLE_DIR / name).read_text(encoding="utf-8") for name in CONSOLE_SCRIPTS)


def stylesheet() -> str:
    """The console's stylesheet, as one text."""
    return "\n".join((CONSOLE_DIR / name).read_text(encoding="utf-8") for name in STYLESHEETS)


def factory_source(name: str) -> str:
    """A factory module's source -- or, for a package, every module in it,
    subpackages included."""
    module = importlib.import_module(f"factory.{name}")
    if not hasattr(module, "__path__"):
        return inspect.getsource(module)
    parts = [inspect.getsource(module)]
    for info in pkgutil.walk_packages(module.__path__, prefix=f"factory.{name}."):
        parts.append(inspect.getsource(importlib.import_module(info.name)))
    return "\n".join(parts)


def factory_files() -> list[Path]:
    """The factory's own Python files: every top-level module, and every module
    of a package that was split out of one (factory/pipeline/, factory/server/).

    Not factory/harnesses/ or factory/roles/: a test that scanned `factory/*.py`
    meant the factory's modules, and still means only those.
    """
    split = ("pipeline", "server")
    return [*sorted(FACTORY_DIR.glob("*.py")),
            *(p for name in split for p in sorted((FACTORY_DIR / name).rglob("*.py")))]


def class_source(cls: type) -> str:
    """A class's source, with every method it inherits from its own codebase.

    `inspect.getsource(cls)` reads one `class` statement. A class assembled
    from mixins keeps its methods elsewhere, and a test asking whether a call
    appears anywhere in it means all of them.
    """
    seen, parts = set(), []
    for klass in cls.__mro__:
        if klass is object or not klass.__module__.startswith("factory"):
            continue
        source = inspect.getsource(klass)
        if source not in seen:
            seen.add(source)
            parts.append(source)
    return "\n".join(parts)


def suite_source() -> str:
    """The test suite's own source: the shared helpers and every test file."""
    tests = ROOT / "tests"
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in [tests / "helpers.py", *sorted(tests.glob("test_*.py"))])
