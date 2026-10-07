"""Tests that cannot fail, read from the test file itself.

Coverage says a line ran. It cannot say whether anything checked what the line
did: a test that calls the code and asserts nothing covers every line it
touches. So a worker sent back for untested lines has an easy way out, and
this is what makes it visible -- a test with no assertion, an assertion that
holds whatever the code does, a test that is switched off, a test that
swallows the failure it was written to catch.

Read by the language's own structure where there is one to hand (Python's
`ast`), and by the shape of the call where there is not. Each finding says
where it is, in words a person reads without opening the file. A language this
module does not read is said nothing about -- which is not the same as clean,
and the caller does not present it as such.
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

#: Words in a call that make it a check: `assert_called_once`, `raises`,
#: `expect`, `fail`. A test calling any of these is asserting something.
_CHECKING = re.compile(r"assert|raises|expect|fail|verify|should|check", re.I)

_SCRIPT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def hollow_tests(path: str, text: str) -> list[str]:
    """What in this test file cannot fail, one sentence each, with its line."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return _python(text)
    if suffix in _SCRIPT:
        return _script(text)
    return []


def _decorated(node: ast.AST, word: str) -> bool:
    for deco in getattr(node, "decorator_list", []):
        if word in ast.unparse(deco).lower():
            return True
    return False


def _python(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        where = f"{node.name} (line {node.lineno})"
        if _decorated(node, "skip") or _decorated(node, "xfail"):
            out.append(f"{where} is switched off, so it checks nothing")
            continue
        checks = False
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assert):
                checks = True
                test = inner.test
                if isinstance(test, ast.Constant) and test.value:
                    out.append(f"{where} asserts a constant, which cannot fail")
                elif (isinstance(test, ast.Compare) and len(test.comparators) == 1
                      and ast.dump(test.left) == ast.dump(test.comparators[0])):
                    out.append(f"{where} compares a value with itself, which cannot fail")
            elif isinstance(inner, ast.Call) and _CHECKING.search(ast.unparse(inner.func)):
                checks = True
            elif isinstance(inner, ast.With) and any(
                    _CHECKING.search(ast.unparse(item.context_expr)) for item in inner.items):
                checks = True
            elif isinstance(inner, ast.ExceptHandler):
                broad = inner.type is None or ast.unparse(inner.type) in ("Exception", "BaseException")
                if broad and all(isinstance(s, ast.Pass) for s in inner.body):
                    out.append(f"{where} catches every exception and ignores it, so a "
                               "failure inside it passes")
        if not checks:
            out.append(f"{where} asserts nothing, so it passes whatever the code does")
    return out


_CASE = re.compile(r"(?m)^\s*(x?it|test)(\.(?:skip|todo|only))?\s*\(\s*(['\"`])(.+?)\3")


def _script(text: str) -> list[str]:
    out: list[str] = []
    cases = list(_CASE.finditer(text))
    for i, match in enumerate(cases):
        body = text[match.end(): cases[i + 1].start() if i + 1 < len(cases) else len(text)]
        line = text.count("\n", 0, match.start()) + 1
        where = f"'{match.group(4)}' (line {line})"
        if match.group(1) == "xit" or match.group(2) in (".skip", ".todo"):
            out.append(f"{where} is switched off, so it checks nothing")
            continue
        if not re.search(r"\bexpect\s*\(|\bassert\b|\.should\b", body):
            out.append(f"{where} asserts nothing, so it passes whatever the code does")
            continue
        if re.search(r"expect\(\s*(true|1|['\"][^'\"]*['\"])\s*\)\s*\.\s*to(Be|Equal)\(\s*\1\s*\)", body):
            out.append(f"{where} expects a constant to equal itself, which cannot fail")
        if re.search(r"catch\s*(\(\s*\w*\s*\))?\s*\{\s*\}", body):
            out.append(f"{where} catches an error and ignores it, so a failure inside it passes")
    return out
