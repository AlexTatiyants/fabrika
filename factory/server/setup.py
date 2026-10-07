"""Setup: the five stations a machine passes before Fabrika can build.

One read that measures everything the setup screen lights a lamp for, and the
handful of actions it offers -- each one something Fabrika can do for you with
your say-so (start Docker, run an install command the route already names,
store and switch on a key, build a harness, write the crew and the limits).
Anything slow runs as a job and is reported by the read while it runs. See
`factory/commissioning.py`.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

from .. import commissioning as cm
from ..config import (ConfigError, api_key_source, mask_key, write_credential,
                      write_executor_kind, write_rework_limits, write_route_enabled)
from .bodies import SetupCrew, SetupDecline, SetupKey, SetupLimits
from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    cfg = ctx.cfg
    routes = ctx.routes
    jobs = cm.Jobs()
    #: The last measurement of this machine. Kept for the process, re-taken on
    #: request; a lamp lit from a reading says when the reading was.
    machine: dict[str, Any] = {}

    def check_machine() -> dict[str, Any]:
        async def work() -> str:
            machine.clear()
            machine.update(await cm.check_machine(cfg))
            return ""
        return jobs.start("machine", work)

    def route_or_404(name: str):
        route = cfg.routes.get(name)
        if route is None:
            raise HTTPException(status_code=404, detail=f"no route named {name!r}")
        return route

    async def harnesses(connected: list[str]) -> list[dict[str, Any]]:
        out = []
        for name in connected:
            route = cfg.routes[name]
            job = jobs.get(f"harness:{name}") or {}
            row = {
                "route": name,
                "label": route.harness_label or route.label or name,
                "account_label": route.account_label or route.label or name,
                "default": name == cfg.default_route,
                "build_image": route.container_build_image,
                "install": list(route.container_install),
                "state": "none", "detail": "",
            }
            if not route.container_install:
                row["state"] = "unsealed"
                row["detail"] = ("This route says nothing about how to install itself in a "
                                 "container, so it cannot run sealed.")
            elif job.get("state") == "running":
                row["state"] = "building"
            elif await cm.harness_ready(cfg, route):
                row["state"] = "ready"
            elif job.get("state") == "failed":
                row["state"], row["detail"] = "failed", job.get("detail") or ""
            out.append(row)
        return out

    @app.get("/api/setup/brief")
    def get_brief() -> dict[str, Any]:
        """Enough for the console to decide, at boot, whether to open on setup:
        nothing measured, nothing slow."""
        return {"finished": bool(cm.read_state(cfg)["finished_at"]),
                "projects": len(ctx.registry.list())}

    @app.get("/api/setup")
    async def get_setup() -> dict[str, Any]:
        if not machine and not jobs.get("machine"):
            check_machine()
        memory = cm.read_state(cfg)
        statuses = await routes.survey()
        connected = cm.connected_routes(cfg, statuses)
        accounts = []
        for status in statuses:
            route = cfg.routes[status["name"]]
            if status["name"] == cfg.default_route or route.kind != "cli":
                continue
            takes_key = cm.route_takes_key(route)
            # A switched-off plan cannot be connected from here; a switched-off
            # key route can, and connecting it is what switches it on.
            if not route.enabled and not takes_key:
                continue
            accounts.append({
                **status,
                "takes_key": takes_key,
                "key_present": bool(route.api_key),
                "key_hint": mask_key(route.api_key),
                "key_from_env": bool(route.key_env and os.environ.get(route.key_env)),
                "key_checkable": bool(route.key_check_url),
                "declined": status["name"] in memory["declined"],
                "connected": status["name"] in connected,
                "job": jobs.get(f"install:{status['name']}") or jobs.get(f"check:{status['name']}"),
            })
        switched_off = [cfg.routes[s["name"]].label or s["name"] for s in statuses
                        if not cfg.routes[s["name"]].enabled
                        and not cm.route_takes_key(cfg.routes[s["name"]])
                        and s["name"] != cfg.default_route]
        default = cfg.routes.get(cfg.default_route)
        return {
            "machine": dict(machine) or None,
            "can_start_docker": cm.can_start_docker(),
            "provider": {
                "route": cfg.default_route,
                "account_label": (default.account_label or default.label) if default else "",
                "base_url": cfg.api.base_url,
                "key_present": bool(cfg.api.api_key),
                "key_hint": mask_key(cfg.api.api_key),
                "key_source": api_key_source(cfg),
                "connected": cfg.default_route in connected,
            },
            "accounts": accounts,
            "switched_off": switched_off,
            "connected": connected,
            "harnesses": await harnesses(connected),
            "executor": cfg.executor.kind,
            "crew": cm.crew_summary(cfg),
            "limits": {"budget_usd": cfg.rework.budget_usd, "max_rounds": cfg.rework.max_rounds,
                       "wall_clock_minutes": cfg.rework.wall_clock_minutes,
                       "confirmed": memory["limits_confirmed"]},
            "projects": len(ctx.registry.list()),
            "finished": bool(memory["finished_at"]),
            "jobs": jobs.as_dict(),
            "busy": jobs.any_running(),
            "config_path": str(cfg.source_path),
        }

    @app.post("/api/setup/machine")
    async def post_machine() -> dict[str, Any]:
        return check_machine()

    @app.post("/api/setup/docker/start")
    async def post_start_docker() -> dict[str, Any]:
        if not cm.can_start_docker():
            raise HTTPException(status_code=409,
                                detail="Fabrika can only start Docker Desktop on a Mac.")

        async def work() -> str:
            said = await cm.start_docker(cfg)
            machine.clear()
            machine.update(await cm.check_machine(cfg))
            return said
        return jobs.start("docker", work)

    @app.post("/api/setup/routes/{name}/install")
    async def post_install(name: str) -> dict[str, Any]:
        route = route_or_404(name)
        if not route.install_command:
            raise HTTPException(status_code=409, detail=f"{name} names no install command.")

        async def work() -> str:
            said = await cm.install_route(route)
            routes.forget(name)
            return said
        return jobs.start(f"install:{name}", work)

    @app.post("/api/setup/routes/{name}/check")
    async def post_check(name: str) -> dict[str, Any]:
        """The crew page's Check, as a job: the first one builds the harness the
        probe runs in, which can take minutes."""
        route_or_404(name)

        async def work() -> str:
            status = await routes.refresh(name)
            if status.get("state") != "ok":
                raise RuntimeError(status.get("detail") or f"{name} is {status.get('state')}.")
            return ""
        return jobs.start(f"check:{name}", work)

    @app.put("/api/setup/routes/{name}/key")
    async def put_key(name: str, body: SetupKey) -> dict[str, Any]:
        """Prove a key with the route's own check, then store it and switch the
        route on. A key that fails is not stored."""
        route = route_or_404(name)
        if not cm.route_takes_key(route):
            raise HTTPException(status_code=409, detail=f"{name} signs in; it takes no key.")
        if os.environ.get(route.key_env):
            raise HTTPException(
                status_code=409,
                detail=(f"{route.key_env} is set in the environment Fabrika was started from, and "
                        "it wins. Unset it before storing a key here, or this one would be "
                        "silently ignored."))
        key = body.api_key.strip()
        if route.key_check_url:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    answer = await client.get(route.key_check_url,
                                              headers={route.key_check_header: key})
            except httpx.HTTPError as exc:
                return {"ok": False, "detail": f"could not reach {route.key_check_url}: {exc}"}
            if answer.status_code != 200:
                try:
                    said = ((answer.json() or {}).get("error") or {}).get("message") or ""
                except (ValueError, AttributeError):
                    said = answer.text[:200]
                return {"ok": False,
                        "detail": f"{route.account_label or name} answered {answer.status_code}"
                                  f"{': ' + said if said else ''}. The key was not saved."}
        write_credential(cfg, route.key_env, key)
        try:
            write_route_enabled(cfg, name, True)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        routes.forget(name)
        return {"ok": True, "detail": ("Key accepted and saved." if route.key_check_url
                                       else "Key saved. The first call will prove it.")}

    @app.post("/api/setup/routes/{name}/decline")
    def post_decline(name: str, body: SetupDecline) -> dict[str, Any]:
        route_or_404(name)
        declined = [n for n in cm.read_state(cfg)["declined"] if n != name]
        if body.declined:
            declined.append(name)
        return cm.write_state(cfg, declined=declined)

    @app.post("/api/setup/harnesses")
    async def post_harnesses() -> dict[str, Any]:
        """Build every connected account's harness, and make real harnesses the
        way agents work -- OpenHands for the key, each plan's own for a plan."""
        connected = cm.connected_routes(cfg, await routes.survey())
        if not connected:
            raise HTTPException(status_code=409, detail="No account is connected, so there is "
                                                        "no harness to build.")
        if cfg.executor.kind != "command":
            write_executor_kind(cfg, "command")
        started = {}
        for name in connected:
            route = cfg.routes[name]
            if not route.container_install or await cm.harness_ready(cfg, route):
                continue
            started[name] = jobs.start(f"harness:{name}",
                                       lambda route=route: cm.build_harness(cfg, route))
        return {"started": sorted(started), "executor": cfg.executor.kind}

    @app.put("/api/setup/crew")
    def put_crew(body: SetupCrew) -> dict[str, Any]:
        try:
            cm.staff(cfg, body.build, body.check, body.preset)
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return cm.crew_summary(cfg)

    @app.put("/api/setup/limits")
    def put_limits(body: SetupLimits) -> dict[str, Any]:
        try:
            write_rework_limits(cfg, budget_usd=body.budget_usd, max_rounds=body.max_rounds,
                                wall_clock_minutes=body.wall_clock_minutes)
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        cm.write_state(cfg, limits_confirmed=True)
        return {"budget_usd": cfg.rework.budget_usd, "max_rounds": cfg.rework.max_rounds,
                "wall_clock_minutes": cfg.rework.wall_clock_minutes, "confirmed": True}

    @app.post("/api/setup/finish")
    def post_finish() -> dict[str, Any]:
        return cm.write_state(cfg, finished_at=time.time())
