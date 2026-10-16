"""What a route can run, said the way a person choosing a model needs it.

A command-line route and a local server carry a short list in `factory.yaml`.
An aggregator carries hundreds, and a dropdown of hundreds is not a choice.
So each route's models are served as rows -- the id, and whatever the route's
own `/models` says about it: context, price, and whether it calls tools,
answers to a schema or takes a thinking level -- for the console's picker to
search and filter by what the agents being placed actually need.

Read from the server, never from a list here: which models exist is the
provider's to say, and a package that knew the names would be a package that
had to be edited for the next one (INV-7). A fact the server does not report
is `None`, not `False`: unknown is not absent, and a filter must not drop a
model for a field its server never sends.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Config, RouteConfig, route_endpoint

#: How long a route's list is kept. A provider adds models in weeks, not
#: minutes; opening the picker should not be a call to it every time.
CACHE_S = 3600.0

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _per_million(value: Any) -> float | None:
    """A per-token price, as dollars per million tokens."""
    try:
        return round(float(value) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def row(raw: dict[str, Any]) -> dict[str, Any]:
    """One `/models` entry as the picker reads it."""
    model = str(raw.get("id") or "")
    params = raw.get("supported_parameters")
    known = isinstance(params, list)

    def has(*names: str) -> bool | None:
        return any(n in params for n in names) if known else None

    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    top = raw.get("top_provider") if isinstance(raw.get("top_provider"), dict) else {}
    context = raw.get("context_length") or top.get("context_length")
    return {
        "id": model,
        "name": str(raw.get("name") or ""),
        "vendor": model.split("/", 1)[0] if "/" in model else "",
        "context": int(context) if isinstance(context, (int, float)) and context > 0 else None,
        "price_in": _per_million(pricing.get("prompt")),
        "price_out": _per_million(pricing.get("completion")),
        "tools": has("tools"),
        "structured": has("structured_outputs", "response_format"),
        "thinking": has("reasoning", "include_reasoning"),
    }


def _listed(route: RouteConfig) -> list[dict[str, Any]]:
    """A route's own short list, with what its config knows about it."""
    thinking = True if route.effort else None
    context = route.context_window or None
    return [{"id": m, "name": "", "vendor": "", "context": context, "price_in": None,
             "price_out": None, "tools": None, "structured": None, "thinking": thinking}
            for m in route.models]


async def catalog(cfg: Config, route: RouteConfig, refresh: bool = False,
                  timeout_s: float = 20.0) -> dict[str, Any]:
    """The models a route offers, as rows, and a sentence when its server could
    not be asked. A route with a list in its config is that list; an API route
    without one is asked at `/models`, with its own key and headers."""
    if route.models or route.kind != "api":
        return {"route": route.name, "models": _listed(route), "source": "config",
                "problem": ""}
    held = _cache.get(route.name)
    if held and not refresh and time.time() - held[0] < CACHE_S:
        return {"route": route.name, "models": held[1], "source": "server", "problem": ""}
    base, key, headers = route_endpoint(cfg, route)
    if key:
        headers = {**headers, "Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            answer = await client.get(f"{base.rstrip('/')}/models", headers=headers)
        listed = answer.json().get("data") if answer.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        listed, problem = None, f"{type(exc).__name__}"
    else:
        problem = "" if listed is not None else f"{base}/models answered {answer.status_code}"
    if not isinstance(listed, list):
        stale = held[1] if held else []
        return {"route": route.name, "models": stale, "source": "server",
                "problem": f"The provider's model list could not be read ({problem or 'no list'})."
                           + (" Showing the last one read." if stale else "")}
    rows = sorted((row(m) for m in listed if isinstance(m, dict) and m.get("id")),
                  key=lambda r: r["id"])
    _cache[route.name] = (time.time(), rows)
    return {"route": route.name, "models": rows, "source": "server", "problem": ""}


def forget(route_name: str = "") -> None:
    """Drop a route's kept list, or every route's."""
    if route_name:
        _cache.pop(route_name, None)
    else:
        _cache.clear()
