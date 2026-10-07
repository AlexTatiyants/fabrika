"""How much of a plan's window a run has used, as reported by the plan.

The budget guard measures dollars, which works for a route billed per token and
does not exist for one metered against a subscription. What bounds those is a
rolling window, and without a gauge a run learns it has reached the limit by
being refused.

Some tools report the window continuously. One of them emits, on every call,
the utilization of two windows and the exact second each resets. That is a
gauge, not an error, and it is what makes a reserve possible here at all --
the same idea as `reserve_usd`, which holds money back so a run can always
afford to write its packet.

Two properties decide the shape of everything below.

**The reading is not ours to compute.** Dollars we can count, because we spent
them. A window we cannot, because the human is using the same plan themselves
-- this file was written on a machine whose owner was running the same tool
interactively while the factory ran. A counted figure would have been wrong by
however much they used, in the direction that matters. So the gauge is read
from the tool and never derived.

**The window is per account, not per feature.** Two features building at once
draw on one window, and so does the human. A meter held per run would let each
of them believe it had the whole thing, which is the failure this store exists
to prevent -- so it lives on disk beside the evidence, shared, and merges
rather than replaces.

Nothing here knows the name of a vendor. Which field carries the windows and
what they are called inside it are `RouteConfig` fields, because a tool that
reports none must be describable as reporting none rather than being a branch
this file is missing. (V-6, INV-7)
"""

from __future__ import annotations

import glob
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RouteConfig
from .files import write_atomic


@dataclass
class WindowReading:
    """One window, as the tool last described it."""

    name: str
    utilization: float = 0.0
    resets_at: float = 0.0
    observed_at: float = 0.0
    #: How long the window is. Some tools name their windows after their length
    #: and some call them `primary` and `secondary`, which tells a reader
    #: nothing -- `label` turns the second kind into the first.
    window_minutes: float = 0.0

    @property
    def label(self) -> str:
        """What to call this window in front of a person.

        The tool's own status screen says "5h limit" and "Weekly limit" for the
        two it calls `primary` and `secondary`. A gauge labelled "primary at
        30%" answers no question anybody has; the length is what makes it mean
        something, and the length is in the payload.
        """
        minutes = int(self.window_minutes or 0)
        if minutes <= 0:
            return self.name.replace("_", " ")
        if minutes == 10080:
            return "weekly"
        if minutes % 1440 == 0:
            days = minutes // 1440
            return "daily" if days == 1 else f"{days}-day"
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"

    @property
    def seconds_to_reset(self) -> float:
        return max(0.0, self.resets_at - time.time()) if self.resets_at else 0.0

    @property
    def stale(self) -> bool:
        """Whether this reading describes a window that has since reset.

        A utilization from before the reset is not merely old, it is wrong in
        the one direction that matters -- it says a run has less room than it
        does, and a ceiling built on it would refuse work the plan would have
        allowed.
        """
        return bool(self.resets_at) and time.time() >= self.resets_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "window_minutes": self.window_minutes,
            "utilization": round(self.utilization, 4),
            "resets_at": self.resets_at,
            "observed_at": self.observed_at,
            "seconds_to_reset": round(self.seconds_to_reset),
            "stale": self.stale,
        }


def read_windows(envelope: dict[str, Any], route: RouteConfig) -> list[WindowReading]:
    """Whatever this tool said about its windows, or nothing.

    `meter_windows_key` is a dotted path to a map of window name to window --
    `{"five_hour": {"utilization": 0.21, "resetsAt": 1788548400}, ...}` in the
    first shape this was measured against. A route that declares no path reports
    nothing, which stays a true statement about some of them.

    The second shape names its windows `primary` and `secondary`, reports
    `used_percent` on a 0-100 scale, and sits beside entries that are not
    windows at all -- a `credits` object, a plan name. So `meter_utilization_scale`
    carries the units, and an entry that does not have the utilization key is
    not a window reading at zero, it is not a window. (`meter_resets_relative`
    is for a countdown rather than a timestamp; the tool measured here reports
    `resets_at` as an absolute second, but its own binary carries both spellings
    and a reading off by fifty-six years is not a failure worth guessing at.)

    None of this is academic: a run lost its entire review panel to a quota it
    had no gauge for, and learned about the ceiling by being refused -- which
    this module's first paragraph already described as the thing it exists to
    prevent.
    """
    if not route.meter_windows_key:
        return []
    windows = _windows_in(envelope, route)
    if windows:
        return windows
    # Not every tool puts the gauge where it puts the answer. One writes it only
    # into its own session log, which is a file on this machine named after an
    # id the call did print -- so the reading is still available, one hop away,
    # and the alternative is a route that reports nothing while the tool knows.
    for record in _sidecar_records(envelope, route):
        windows = _windows_in(
            record, route,
            observed_at=_record_time(record, route) or _SIDECAR_MTIME.get(route.name))
        if windows:
            return windows
    return []


def _record_time(record: dict[str, Any], route: RouteConfig) -> float:
    """When this record was written, by its own account.

    A session log grows while the tool runs, so its mtime dates the last line
    and not the last reading -- a long session can leave a gauge hours old
    inside a file touched seconds ago, and a card dated by mtime says "plan
    read just now" directly above "reset since last read".
    """
    if not route.meter_observed_key:
        return 0.0
    raw = _dotted(record, route.meter_observed_key)
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


#: When each route's session log was last written, filled in as it is read. A
#: module-level note rather than a return value because `read_windows` fans the
#: records through `_windows_in` one at a time and the timestamp belongs to the
#: file, not to any record in it.
_SIDECAR_MTIME: dict[str, float] = {}


def _dotted(node: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


#: Where copies of a tool's session logs are kept once they come out of the
#: container the tool ran in. The tool runs sealed, so the log it writes lands
#: in that container's home and goes when the container does -- and the gauge,
#: which reads the log by path afterwards, found nothing and stayed on a window
#: that had ended. Every visit to the crew page then spent a turn to refresh a
#: reading that the refresh could never reach. Set from the config's evidence
#: path by whatever reads or records a gauge (`use_mirror`).
_MIRROR: list[Path] = []
#: Enough to hold the newest reading through a busy day; each file is one call.
MIRROR_KEEP = 60


def use_mirror(evidence_path: str | Path) -> Path:
    base = Path(evidence_path) / "sidecars"
    _MIRROR[:] = [base]
    return base


def sidecar_dir(route: RouteConfig) -> str:
    """The fixed part of the log's path under the tool's home: `.codex/sessions`
    for `~/.codex/sessions/**/rollout-*.jsonl`. Empty when the pattern is not
    under a home, because then there is no home in a container to copy from."""
    pattern = (route.meter_sidecar_glob or "").strip()
    if not pattern.startswith("~/"):
        return ""
    fixed: list[str] = []
    for part in pattern[2:].split("/")[:-1]:
        if any(c in part for c in "*?[{"):
            break
        fixed.append(part)
    return "/".join(fixed)


def mirror_home(base: str | Path, route_name: str) -> Path:
    """The stand-in home a route's copied logs sit under, laid out as the tool's
    own home is, so the same pattern finds them."""
    return Path(base) / re.sub(r"[^A-Za-z0-9._-]+", "-", route_name)


def prune_mirror(base: str | Path, route_name: str, keep: int = MIRROR_KEEP) -> None:
    """Keep the newest `keep` logs. Only the newest is ever read for the gauge;
    the rest are a short history, not an archive."""
    root = mirror_home(base, route_name)
    try:
        files = sorted((f for f in root.rglob("*") if f.is_file()),
                       key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    for stale in files[:-keep] if keep else files:
        try:
            stale.unlink()
        except OSError:
            pass


def _sidecar_patterns(pattern: str, route: RouteConfig) -> list[str]:
    """The pattern as the tool's own home has it, and again under the copies."""
    out = [str(Path(pattern).expanduser())]
    if pattern.startswith("~/"):
        out += [str(mirror_home(base, route.name) / pattern[2:]) for base in _MIRROR]
    return out


def _sidecar_records(
    envelope: dict[str, Any], route: RouteConfig, *, latest: bool = False,
) -> list[dict[str, Any]]:
    """The tool's own log, newest record first, or nothing.

    `meter_sidecar_glob` is a path pattern whose `{placeholders}` are dotted
    paths into the envelope -- `{thread_id}` picks the log of one specific call.
    Newest first because a session log is append-only and the last reading in it
    is the current one.

    `latest` widens every placeholder to `*` instead, which asks a different and
    more useful question: not "what did our call see" but "what is the newest
    thing this tool has written about the plan". Those logs are also written when
    the human uses the tool themselves, so the freshest reading on the machine is
    usually not from us -- and reading it costs nothing, which is what makes a
    gauge that refreshes on its own legitimate. Opening a screen is not consent
    to spend a turn; it is consent to read a file.
    """
    pattern = (route.meter_sidecar_glob or "").strip()
    if not pattern:
        return []
    for placeholder in set(re.findall(r"\{([^{}]+)\}", pattern)):
        value = "*" if latest else _dotted(envelope, placeholder)
        if not value:
            return []
        pattern = pattern.replace("{" + placeholder + "}", str(value))
    try:
        found = {m for each in _sidecar_patterns(pattern, route)
                 for m in glob.glob(each, recursive=True)}
        matches = sorted(found, key=lambda p: Path(p).stat().st_mtime)
    except OSError:
        return []
    if not matches:
        return []
    try:
        newest = Path(matches[-1])
        lines = newest.read_text(encoding="utf-8").splitlines()
        # When the tool wrote this, which is when it last measured. Carried so a
        # reading lifted off disk is not stamped with the moment we happened to
        # read the file -- "plan read just now" over a window that ended hours
        # ago is two true sentences that add up to a false one.
        _SIDECAR_MTIME[route.name] = newest.stat().st_mtime
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def _windows_in(envelope: dict[str, Any], route: RouteConfig,
                observed_at: float | None = None) -> list[WindowReading]:
    """One record's worth of windows, or nothing if it carries none.

    `observed_at` is when the reading was taken, which is not always now: a
    reading lifted out of a tool's session log was measured when the tool last
    ran, and stamping it with the moment we opened the file makes an old number
    look current.
    """
    node = _dotted(envelope, route.meter_windows_key)
    if not isinstance(node, dict):
        return []

    now = time.time() if observed_at is None else float(observed_at)
    out: list[WindowReading] = []
    for name, body in node.items():
        if not isinstance(body, dict):
            continue
        # A sibling that is not a window -- the `credits` object next to
        # `primary` and `secondary` -- has no utilization to read, and defaulting
        # it to zero would put a gauge on the screen reading "0% used" for a
        # thing that is not a gauge.
        if route.meter_utilization_key not in body:
            continue
        try:
            utilization = float(body.get(route.meter_utilization_key) or 0.0)
        except (TypeError, ValueError):
            continue
        utilization *= route.meter_utilization_scale or 1.0
        try:
            resets = float(body.get(route.meter_resets_key) or 0.0)
        except (TypeError, ValueError):
            resets = 0.0
        # A countdown becomes a timestamp here and nowhere else, so everything
        # downstream -- `stale`, `seconds_to_reset`, the merge across processes
        # -- keeps comparing one kind of number. A reading taken now and read
        # back in ten minutes has to still mean the same instant.
        if resets and route.meter_resets_relative:
            resets += now
        try:
            minutes = float(body.get(route.meter_window_minutes_key) or 0.0)
        except (TypeError, ValueError):
            minutes = 0.0
        out.append(WindowReading(
            name=str(name), utilization=utilization, resets_at=resets, observed_at=now,
            window_minutes=minutes))
    return sorted(out, key=lambda w: w.name)


def read_credits(envelope: dict[str, Any], route: RouteConfig) -> dict[str, Any]:
    """Whatever this tool says about the balance behind the windows, verbatim.

    A separate pool from the rolling windows, and the distinction is the whole
    reason this exists. A run lost every review agent at once to "Your workspace
    is out of credits" while both of that account's windows had room -- 100%
    left on the five-hour and 70% on the weekly. A reserve built on utilization
    would have sailed into that at 0% used.

    Read and recorded; nothing acts on it. The field seen so far reports
    `has_credits: false` on an account whose calls then succeed, so it may mean
    "no balance configured" rather than "exhausted", and the tool's own status
    screen warns its limits may be stale. Letting that boolean stop a run would
    refuse work the plan allows -- the failure `WindowReading.stale` already
    warns about. So it is carried to where a human can watch it behave across a
    refill, and made a ceiling only once somebody knows what it means.

    Returned as the tool wrote it. This module does not know what a credit is.
    """
    if not route.meter_credits_key:
        return {}
    found = _dotted(envelope, route.meter_credits_key)
    if isinstance(found, dict):
        return dict(found)
    for record in _sidecar_records(envelope, route):
        found = _dotted(record, route.meter_credits_key)
        if isinstance(found, dict):
            return dict(found)
    return {}


def read_limit(route: RouteConfig) -> dict[str, Any]:
    """The limit this tool last said it hit, from its newest log, or nothing.

    Different in kind from `has_credits: false`, which `read_credits` records
    and nothing acts on because it has been seen on accounts whose calls then
    succeed. This is the tool reporting that a call was refused and naming the
    limit that refused it. Measured: every Codex session on a day its workspace
    had run dry carried `rate_limit_reached_type:
    workspace_member_credits_depleted` with both windows `null` -- so the gauge,
    finding no windows, recorded nothing, and a nineteen-hour-old weekly bar
    went on standing for a route refusing every call.

    Returns `{"reached": <the tool's words>, "observed_at": <when>}` from the
    newest record that carries the tool's rate-limit object at all, with
    `reached` empty when that record names no limit. An empty `reached` is
    information: it is how a call that succeeded after a refill clears the
    last one's refusal.
    """
    key = (route.meter_limit_key or "").strip()
    if not key or not route.meter_windows_key:
        return {}
    for record in _sidecar_records({}, route, latest=True):
        if _dotted(record, route.meter_windows_key) is None:
            continue
        reached = _dotted(record, key)
        return {
            "reached": str(reached or ""),
            "observed_at": _record_time(record, route) or _SIDECAR_MTIME.get(route.name) or 0.0,
        }
    return {}


def read_from_disk(route: RouteConfig) -> tuple[list[WindowReading], dict[str, Any]]:
    """The newest gauge this tool has written down, without asking it anything.

    The reading a route can offer between calls. A tool that keeps a session log
    writes its windows there every time it runs -- for the factory and for the
    human at the same terminal -- so the number on the screen can be current
    without a run happening and without a turn being spent.

    A tool that keeps no such log returns nothing, and nothing is the honest
    answer: its gauge is only as fresh as the last call anybody made through it,
    and a card that says so is worth more than one that implies otherwise.
    """
    if not route.meter_windows_key and not route.meter_credits_key:
        return [], {}
    windows: list[WindowReading] = []
    credits: dict[str, Any] = {}
    for record in _sidecar_records({}, route, latest=True):
        if not windows and route.meter_windows_key:
            # Dated by the record's own account of when it was written. The
            # file's mtime dates the newest *line*, which in a session still
            # being appended to is not the newest reading.
            windows = _windows_in(
                record, route,
                observed_at=_record_time(record, route) or _SIDECAR_MTIME.get(route.name))
        if not credits and route.meter_credits_key:
            found = _dotted(record, route.meter_credits_key)
            if isinstance(found, dict):
                credits = dict(found)
        if windows and (credits or not route.meter_credits_key):
            break
    return windows, credits


class DrawStore:
    """What a run has historically drawn from each plan, kept beside the gauge.

    The gauge says how much of a window is left. This says how much a build
    needs. Neither is useful alone for the only question worth asking before
    committing work -- "will this finish?" -- and the second cannot be reasoned
    about, only observed: a run's draw depends on the feature, the repair rounds
    it takes and which agents ride which route.

    Kept as the last few observations rather than a running average, because the
    interesting statistic is not the mean. A run that would fit on average and
    not on a bad day is a run that fails halfway, so the figure this hands out
    is the worst recent draw, not the typical one.
    """

    #: Enough to see a bad day, few enough that a change of model or plan works
    #: its way through in a week rather than a quarter.
    KEEP = 8

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def all(self) -> dict[str, list[float]]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, list[float]] = {}
        for route, seen in (body.get("routes") or {}).items():
            values = [float(v) for v in (seen or []) if isinstance(v, (int, float))]
            if values:
                out[route] = values[-self.KEEP:]
        return out

    def expected(self, route: str) -> float:
        """The draw to plan against, or 0.0 when this route has never been
        measured. Zero means "no basis", and a caller must not read it as
        "free" -- `observations` is how you tell those apart."""
        seen = self.all().get(route) or []
        return max(seen) if seen else 0.0

    def observations(self, route: str) -> int:
        return len(self.all().get(route) or [])

    def record(self, drawn: dict[str, float]) -> None:
        """Append one run's draw. Never fatal; merged, never a wholesale
        rewrite, for the reason `MeterStore` gives."""
        if not drawn:
            return
        body: dict[str, Any] = {"routes": {}}
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("routes"), dict):
                body = existing
        except (OSError, json.JSONDecodeError):
            pass
        routes = body.setdefault("routes", {})
        for route, value in drawn.items():
            seen = [float(v) for v in (routes.get(route) or []) if isinstance(v, (int, float))]
            seen.append(round(float(value), 4))
            routes[route] = seen[-self.KEEP:]
        try:
            write_atomic(self.path, json.dumps(body, indent=2))
        except OSError:
            pass


class MeterStore:
    """The last reading for every window of every route, on disk.

    Merged rather than replaced on write, and for a concrete reason: this file
    sits beside the evidence store, which is the working directory's `.factory`
    by default, and more than one process reaches it. A wholesale rewrite meant
    that whichever one wrote last erased what the others had measured -- which
    is how a test suite once deleted a human's connection checks.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reading ----------------------------------------------------------

    def all(self) -> dict[str, list[WindowReading]]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        out: dict[str, list[WindowReading]] = {}
        for route, windows in (body.get("routes") or {}).items():
            readings = []
            for w in windows or []:
                try:
                    readings.append(WindowReading(
                        name=str(w.get("name") or ""),
                        utilization=float(w.get("utilization") or 0.0),
                        resets_at=float(w.get("resets_at") or 0.0),
                        observed_at=float(w.get("observed_at") or 0.0),
                        window_minutes=float(w.get("window_minutes") or 0.0),
                    ))
                except (TypeError, ValueError):
                    continue
            if readings:
                out[route] = readings
        return out

    def latest(self, route: str) -> list[WindowReading]:
        return self.all().get(route, [])

    def worst(self, route: str) -> WindowReading | None:
        """The window closest to full, ignoring any that has since reset.

        A route is constrained by its tightest window, not its average: being
        at 11% of a week and 96% of five hours is being nearly out of room.
        """
        live = [w for w in self.latest(route) if not w.stale]
        return max(live, key=lambda w: w.utilization) if live else None

    def limit(self, route: str) -> dict[str, Any]:
        """The limit this route last named, or nothing. See `read_limit`."""
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        found = (body.get("limits") or {}).get(route)
        return dict(found) if isinstance(found, dict) and found.get("reached") else {}

    def record_limit(self, route: str, limit: dict[str, Any]) -> None:
        """Set or clear it. Never older over newer: a record read from an
        older log must not reinstate a refusal a newer call has cleared."""
        if not limit:
            return
        body: dict[str, Any] = {"routes": {}}
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                body = existing
        except (OSError, json.JSONDecodeError):
            pass
        limits = body.setdefault("limits", {})
        held = limits.get(route) or {}
        if float(held.get("observed_at") or 0) > float(limit.get("observed_at") or 0):
            return
        # Cleared, not deleted. The cleared record is what carries the time of
        # the success, and without it the next older reading read off disk has
        # nothing to lose to and reinstates a refusal that is already over.
        limits[route] = dict(limit)
        self._write(body)

    def credits(self, route: str) -> dict[str, Any]:
        """The last thing this route said about its balance, or nothing."""
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        found = (body.get("credits") or {}).get(route)
        return dict(found) if isinstance(found, dict) else {}

    # -- writing ----------------------------------------------------------

    def record(self, route: str, windows: list[WindowReading],
               credits: dict[str, Any] | None = None) -> None:
        """Never fatal, and never a wholesale rewrite. See the class docstring."""
        if not windows and not credits:
            return
        body: dict[str, Any] = {"routes": {}}
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("routes"), dict):
                body = existing
        except (OSError, json.JSONDecodeError):
            pass
        if credits:
            body.setdefault("credits", {})[route] = {
                **credits, "observed_at": time.time()}
        if not windows:
            self._write(body)
            return
        body.setdefault("routes", {})[route] = [w.as_dict() for w in windows]
        self._write(body)

    def _write(self, body: dict[str, Any]) -> None:
        try:
            write_atomic(self.path, json.dumps(body, indent=2))
        except OSError:
            # Losing a measurement is worse than not taking it and better than
            # losing the call that produced it.
            pass
