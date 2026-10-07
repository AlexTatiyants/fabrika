"""What a compose file asks for from `.env` files, and what stands in for them.

A compose service that says `env_file: .env` cannot even be loaded where that
file is absent, and it is usually absent: the file holds a developer's secrets
and is ignored by git, so a clean checkout never has it. Checks are run from
clean checkouts. The project's own answer is the `.env.example` beside it,
which is committed and says which variables exist and what harmless values look
like -- so that is what stands in, in the stack the checks run in, and nothing
is written into the person's repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: Siblings that say what a missing env file would have held, best first.
EXAMPLE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def declared(body: Any) -> list[tuple[str, bool]]:
    """`(path, required)` for each env file a service declares.

    Compose spells this three ways: a string, a list of strings, and a list of
    `{path, required}` mappings.
    """
    raw = body.get("env_file") if isinstance(body, dict) else None
    if isinstance(raw, (str, dict)):
        raw = [raw]
    out: list[tuple[str, bool]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            out.append((item, True))
        elif isinstance(item, dict) and item.get("path"):
            out.append((str(item["path"]), item.get("required", True) is not False))
    return out


def dotenv(text: str) -> dict[str, str]:
    """`KEY=value` lines. Comments and blanks are skipped, quotes are removed."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def example_for(directory: Path, path: str) -> Path | None:
    """The committed file that says what `path` would hold, if there is one."""
    target = directory / path
    candidates = [target.with_name(target.name + s) for s in EXAMPLE_SUFFIXES]
    candidates += [target.with_name(f"{target.name.lstrip('.')}.example"),
                   target.with_name("example" + target.name)]
    return next((c for c in candidates if c.is_file()), None)


def _environment_keys(body: dict[str, Any]) -> set[str]:
    env = body.get("environment")
    if isinstance(env, dict):
        return {str(k) for k in env}
    if isinstance(env, list):
        return {str(item).partition("=")[0] for item in env}
    return set()


def resolve(directory: Path, body: Any) -> tuple[list[Any] | None, dict[str, str], list[str]]:
    """How one service's env files are met in a clean checkout.

    Returns `(files, values, unmet)`. `files` is the list to declare in place of
    the project's, or `None` when every file it names is there and nothing needs
    changing. `values` are the variables to supply directly for the files that
    were not, taken from their examples and never overriding what the service
    already sets itself. `unmet` names required files that are absent with
    nothing to stand in for them.
    """
    entries = declared(body)
    if not entries:
        return None, {}, []
    keep: list[str] = []
    values: dict[str, str] = {}
    unmet: list[str] = []
    changed = False
    for path, required in entries:
        if (directory / path).is_file():
            keep.append(path)
            continue
        changed = True
        example = example_for(directory, path)
        if example is not None:
            try:
                values.update(dotenv(example.read_text(encoding="utf-8")))
            except OSError:
                if required:
                    unmet.append(path)
        elif required:
            unmet.append(path)
    if not changed:
        return None, {}, []
    own = _environment_keys(body) if isinstance(body, dict) else set()
    return keep, {k: v for k, v in values.items() if k not in own}, unmet
