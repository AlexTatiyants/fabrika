"""The provider, the editor link, and the crew: each agent's model, route and
prompt, what each has spent, the routes' gauges, and the way out of the sealed
networks.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

from .. import asbuilt, egress
from ..config import (api_key_source, ConfigError, credentials_path, delete_role, independence_problems,
                      INDEPENDENT_OF, LEVELS, mask_key, prompt_path, staffing_suggestions, sync_default_route,
                      config_target_path, team_of, write_api_settings, write_credential,
                      write_editor_url, write_role, write_role_prompt, write_staffing)

from .bodies import (EditorUpdate, NewReviewAgent, PromptBody, ProviderUpdate, RoleUpdate,
                     StaffingUpdate)
from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    """Provider, editor, roles, routes, meters and egress."""
    cfg = ctx.cfg
    registry = ctx.registry
    llm = ctx.llm
    routes = ctx.routes

    # ================================================================
    # provider
    # ================================================================

    def provider_state() -> dict[str, Any]:
        """Presence and a four-character tail. The key itself is never returned
        to a client, including this one."""
        source = api_key_source(cfg)
        return {
            "base_url": cfg.api.base_url,
            "key_present": bool(cfg.api.api_key),
            "key_hint": mask_key(cfg.api.api_key),
            "key_source": source,
            "credentials_file": str(credentials_path(cfg)),
            "config_file": str(config_target_path(cfg)),
        }

    @app.get("/api/provider")
    def get_provider() -> dict[str, Any]:
        return provider_state()

    @app.put("/api/provider")
    async def put_provider(body: ProviderUpdate) -> dict[str, Any]:
        if body.base_url is not None and body.base_url.strip():
            write_api_settings(cfg, base_url=body.base_url.strip())
        if body.api_key is not None:
            key = body.api_key.strip()
            if api_key_source(cfg) == "environment" and key:
                raise HTTPException(
                    status_code=409,
                    detail=("an environment variable is supplying the key, and it wins. "
                            "Unset it before storing one here, or the stored value would be "
                            "silently ignored."),
                )
            write_credential(cfg, "api_key", key)
            cfg.api.api_key = key
        # The default route is a copy of the api block; a changed key that never
        # reached it would leave the crew table saying "not signed in" over a
        # provider that works.
        sync_default_route(cfg)
        routes.forget()
        # The Authorization header is baked into the cached client at first use.
        await llm.reset_client()
        return provider_state()

    @app.put("/api/editor")
    def put_editor(body: EditorUpdate) -> dict[str, Any]:
        """The one setting on this screen: how a file opens for the person
        reading a packet, not for the pipeline. Not a secret and not scoped to
        a project, so it is a top-level scalar in the same file the provider's
        `base_url` lives in -- see `write_editor_url`."""
        write_editor_url(cfg, body.editor_url.strip())
        return {"editor_url": cfg.editor_url}

    @app.post("/api/provider/test")
    async def test_provider() -> dict[str, Any]:
        """Two cheap GETs, and an honest account of what they proved.

        Not every failure is "rejected the key" -- one is that no key was sent
        at all. An error that cannot tell you which of three things went wrong
        is not much better than no error.
        """
        base = cfg.api.base_url.rstrip("/")
        key = cfg.api.api_key

        if not key:
            return {
                "ok": False, "checked": "nothing", "sent": "",
                "detail": ("No key is set, so nothing was sent. Paste one above and press Save "
                           "before testing — Test reads the saved key, not the field."),
            }

        headers = {**cfg.api.headers, "Authorization": f"Bearer {key}"}

        async def probe(path: str):
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    return await client.get(base + path, headers=headers), None
            except httpx.HTTPError as exc:
                return None, str(exc)

        def provider_message(response) -> str:
            try:
                body = response.json() or {}
            except ValueError:
                return response.text[:200]
            error = body.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or "")[:200]
            return str(error or "")[:200] or response.text[:200]

        sent = mask_key(key)
        keyed, error = await probe("/key")
        if keyed is not None and keyed.status_code == 200:
            return {"ok": True, "checked": "credential", "sent": sent,
                    "detail": f"{base} accepted the key."}

        if keyed is not None and keyed.status_code in (401, 403):
            message = provider_message(keyed)
            hint = ""
            if "cookie" in message.lower():
                hint = (" The provider says it saw no credential at all, which means the header "
                        "did not arrive — not that the key is wrong.")
            elif not key.startswith("sk-or-") and "openrouter" in base:
                hint = (f" The key sent starts with {key.split('-')[0]!r}; OpenRouter keys begin "
                        "with 'sk-or-v1-'. Check you did not paste a key for a different provider.")
            return {"ok": False, "checked": "credential", "sent": sent,
                    "detail": f"{base} returned {keyed.status_code}: {message}{hint}"}

        models, error = await probe("/models")
        if models is None:
            return {"ok": False, "checked": "nothing", "sent": sent,
                    "detail": f"could not reach {base}: {error}"}
        if models.status_code in (401, 403):
            return {"ok": False, "checked": "credential", "sent": sent,
                    "detail": f"{base} returned {models.status_code}: {provider_message(models)}"}
        if models.status_code != 200:
            return {"ok": False, "checked": "nothing", "sent": sent,
                    "detail": f"{base}/models returned {models.status_code}: {models.text[:200]}"}
        return {
            "ok": True, "checked": "reachability", "sent": sent,
            "detail": (f"{base} is reachable, but it has no endpoint that exercises a key, so the "
                       "credential itself was not proven. The first real call will tell you."),
        }

    # ================================================================
    # agents
    # ================================================================

    REVIEW_STARTER = (
        "# {name}\n\n"
        "You are one of the agents operating a software factory. Your job is to review a finished\n"
        "feature from one angle and contribute findings.\n\n"
        "You see everything: the spec, the repository as it was, every file written, every decision\n"
        "logged, every worker disclosure, the gates, the adversarial probes that were run against\n"
        "this code and how they came out, and the computed traceability. Every other review agent\n"
        "sees exactly the same evidence, so do not try to cover their ground. Cover yours,\n"
        "thoroughly.\n\n"
        "## Rules\n\n"
        "- Every finding cites the specific thing it is about: a path, a symbol, a criterion id, a\n"
        "  decision id. A finding without evidence cannot be acted on. A finding with fabricated\n"
        "  evidence is worse than silence, because it burns the credibility of every other finding\n"
        "  in the packet.\n"
        "- Severity is a claim about a human's time. `blocker` means do not ship. `major` means a\n"
        "  human must rule on it. A packet where everything is major is a packet nobody reads.\n"
        "- Use `conceded` for what you examined and found sound. Concessions are what make the rest\n"
        "  of your case credible.\n"
        "- If the work is fine from your angle, say so and let your findings be thin. An agent that\n"
        "  always finds something carries no information.\n\n"
        "## Your angle\n\n"
        "TODO: describe exactly what this agent is looking for, and what it should ignore.\n"
    )

    def role_usage() -> dict[str, dict[str, Any]]:
        """What each role has spent, summed over every run in every project.

        Dollars AND the calls no dollar figure covers. A subscription route
        reports no cost, so an agent that ran fifty-two times on one comes
        back `cost: 0.0` and nothing else -- which the crew screen would show
        as a dash, indistinguishable from an agent that never ran at all. The
        console has a branch for saying "N turns" instead, and it fires only if
        these two fields get past here.
        """
        totals: dict[str, dict[str, Any]] = {}
        for project in registry.list():
            for feature_id in project.feature_ids():
                usage = project.feature_store(feature_id).payload("usage") or {}
                for name, row in (usage.get("roles") or {}).items():
                    acc = totals.setdefault(name, {
                        "calls": 0, "total_tokens": 0, "cost": 0.0,
                        "unbilled_calls": 0, "turns": 0})
                    acc["calls"] += row.get("calls") or 0
                    acc["total_tokens"] += row.get("total_tokens") or 0
                    acc["cost"] = round(acc["cost"] + (row.get("cost") or 0.0), 6)
                    acc["unbilled_calls"] += row.get("unbilled_calls") or 0
                    acc["turns"] += row.get("turns") or 0
            # The as-built's readings run outside any feature's build, so its
            # two agents never reach a `usage` record. Each reading carries its
            # own split by agent -- on a person's press in the project ledger,
            # beside a feature in that feature's.
            stores = [project.store] + [project.feature_store(f) for f in project.feature_ids()]
            for store in stores:
                for record in store.all("as_built"):
                    for name, row in ((record.get("meta") or {}).get("by_role") or {}).items():
                        acc = totals.setdefault(name, {
                            "calls": 0, "total_tokens": 0, "cost": 0.0,
                            "unbilled_calls": 0, "turns": 0})
                        acc["calls"] += row.get("calls") or 0
                        acc["total_tokens"] += (row.get("total_tokens")
                                                or (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0))
                        acc["cost"] = round(acc["cost"] + (row.get("cost_usd") or 0.0), 6)
                        acc["unbilled_calls"] += row.get("unbilled_calls") or 0
                        acc["turns"] += row.get("unbilled_calls") or 0
        return totals

    def as_built_unsplit() -> dict[str, int]:
        """Readings from before they said which agent made each call: counted
        here as a total, since dividing them between the two would be a guess."""
        out = {"readings": 0, "calls": 0}
        for project in registry.list():
            for record in project.store.all("as_built"):
                meta = record.get("meta") or {}
                if meta.get("calls") and not meta.get("by_role"):
                    out["readings"] += 1
                    out["calls"] += meta["calls"]
        return out

    # Called by name, in a fixed order, by the orchestrator. Everything else is
    # a review agent, declared in config.
    PIPELINE_ROLES = [
        "surveyor", "resurvey", "scout", "interrogator", "spec_writer", "spec_checker",
        "architect", "plan_checker", "worker",
        "integrator", "oracle", "breaker", "arbiter", "repairer",
        "simplifier", "rapporteur", "reader", "cartographer",
    ]
    # Called by name as well, but for a project rather than inside a feature's
    # run: a red check's diagnosis, a drafted guide, a test runner's report
    # command. Not stations on the diagram, and not "never called" either.
    PROJECT_ROLES = ["diagnose", "guide_writer", "reporter"]

    @app.get("/api/roles")
    def list_roles() -> dict[str, Any]:
        usage = role_usage()
        roles = []
        called = PIPELINE_ROLES + PROJECT_ROLES
        for name, role in cfg.roles.items():
            path = prompt_path(cfg, name)
            roles.append({
                **role.model_dump(mode="json"),
                "name": name,
                "kind": "review" if role.review else ("pipeline" if name in called else "unused"),
                # Blue or red, decided by what the agent does. The diagram and
                # the Agent configuration screen both draw it from here.
                "team": team_of(name, role.review),
                # Whether its model comes from its level rather than its own block.
                "follows_level": bool(role.level and cfg.staffing and not role.pinned),
                "prompt_path": str(path),
                "prompt_exists": path.exists(),
                # Whose prompt this is, when it is not this role's own. The
                # screen derived the path from the name and reported a shared
                # prompt as a missing one -- "this agent will fail mid-run", of
                # an agent that runs.
                "prompt_role": role.prompt or name,
                "prompt": path.read_text(encoding="utf-8") if path.exists() else "",
                "usage": usage.get(name, {"calls": 0, "total_tokens": 0, "cost": 0.0,
                                          "unbilled_calls": 0, "turns": 0}),
            })
        roles.sort(key=lambda r: (r["kind"] != "pipeline", r["name"]))
        return {
            "roles": roles,
            # The lanes, and whether the split they exist to keep still holds.
            # Here rather than only on `/api/config` because this is what the
            # crew screen reads, and a guarantee nobody can see is one nobody
            # checks -- which is the whole failure this was built against.
            # Who is working now on an as-built reading, and on which part of it.
            "as_built_running": asbuilt.now_running(),
            "as_built_unsplit": as_built_unsplit(),
            "fallbacks": {name: lane.model_dump() for name, lane in cfg.fallbacks.items()},
            "independence": independence_problems(cfg),
            "config_path": str(config_target_path(cfg)),
            "roles_dir": str(cfg.roles_path),
            "api_key_present": bool(cfg.api.api_key),
            "base_url": cfg.api.base_url,
            "executor": cfg.executor.kind,
        }

    def staffing_state() -> dict[str, Any]:
        suggested = staffing_suggestions(cfg)
        return {
            "staffing": cfg.staffing.model_dump() if cfg.staffing else None,
            "levels": {r: {lv: c.model_dump() for lv, c in body.items()}
                       for r, body in cfg.levels.items()},
            # What the screen offers before anything is chosen. From the
            # shipped example, which is the one place a preset may name a model.
            "presets": suggested["levels"],
            "suggested": suggested["roles"],
            "level_names": list(LEVELS),
            "default_route": cfg.default_route,
            "roles": [{
                "name": name,
                "team": team_of(name, role.review),
                "review": role.review,
                "enabled": role.enabled,
                "level": role.level,
                "side": role.side,
                "pinned": role.pinned,
                # What runs now, which is what a change is measured against.
                "route": role.route or cfg.default_route,
                "model": role.model,
                "reasoning_effort": role.reasoning_effort,
            } for name, role in cfg.roles.items()],
            "independence": independence_problems(cfg),
            # Who must not share a family with whom, so the screen can say so
            # before the write rather than after it.
            "independent_of": {name: list(others) for name, others in INDEPENDENT_OF.items()},
            "config_path": str(config_target_path(cfg)),
        }

    @app.get("/api/staffing")
    def get_staffing() -> dict[str, Any]:
        """Who serves each side, what each level means on each route, and where
        every agent sits -- with the presets the screen starts from."""
        return staffing_state()

    @app.put("/api/staffing")
    def put_staffing(body: StaffingUpdate) -> dict[str, Any]:
        roles = {name: r.model_dump() for name, r in body.roles.items()}
        try:
            write_staffing(cfg, body.staffing, body.levels, roles)
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {**staffing_state(), "written": str(config_target_path(cfg))}

    @app.get("/api/routes")
    async def list_routes() -> dict[str, Any]:
        """Every route and what is known about it, for free.

        Installation is measured on every call -- it is a `which`, and it is how
        a card stops saying "not found" a second after the install finishes.
        Whether anyone is signed in is not measured here: that costs a turn
        against a real plan, and opening a screen is not consent to spend one.
        """
        return {"routes": await routes.survey(), "default": cfg.default_route}

    @app.get("/api/meters")
    async def route_meters() -> dict[str, Any]:
        """What each plan says is left, refreshed from disk. Costs nothing.

        Separate from `/api/routes`, which spawns a `which` per route. This one
        reads the tools' own session logs, so a screen can keep a gauge honest
        on a timer without spending a turn on anyone's plan.
        """
        return {"routes": routes.gauges()}

    @app.get("/api/egress")
    async def egress_status() -> dict[str, Any]:
        """The one way out of the sealed networks: whether it is up, and what
        went through it or was stopped at it in the last day.

        Read-only. It never starts the proxy -- a screen being opened is not a
        reason to change what runs on this machine.
        """
        return await egress.status(cfg.docker)

    @app.post("/api/egress/start")
    async def egress_start() -> dict[str, Any]:
        """Start the proxy now rather than at the next build. Also how a stale
        one (an older script, or started differently) is brought current."""
        problem = await egress.ensure_proxy(cfg.docker)
        if problem:
            raise HTTPException(status_code=502, detail=problem)
        return await egress.status(cfg.docker)

    @app.post("/api/egress/test")
    async def egress_test() -> dict[str, Any]:
        """Prove the way out: a throwaway container with no settings reaches a
        registry over HTTPS and GitHub over SSH. Starts the proxy if it has to,
        because there is nothing to test otherwise."""
        await egress.self_test(cfg.docker)
        return await egress.status(cfg.docker)

    @app.post("/api/meters/refresh")
    async def refresh_meters() -> dict[str, Any]:
        """Every plan's headroom, brought current -- spending a turn only where
        that is the only way to get one.

        Separate from `GET /api/meters`, which is free and polled. This one is
        called at the two moments that earn it: a human arriving at the board,
        and a build about to be committed. A route whose tool writes its gauge
        down costs nothing here either; only one that reports it inside a call,
        and whose window has since reset, spends the probe.
        """
        return {"routes": await routes.gauges_live()}

    @app.post("/api/routes/{name}/check")
    async def check_route(name: str) -> dict[str, Any]:
        """Run one real call and say plainly which of three things is wrong.

        Not installed, installed but nobody signed in, and installed-signed-in-
        but-broken need three different sentences. Run together as "route
        unavailable" none of them tells a human what to do next.
        """
        if name not in cfg.routes:
            raise HTTPException(status_code=404, detail=f"no route {name!r}")
        return await routes.refresh(name)

    @app.patch("/api/roles/{name}")
    def patch_role(name: str, body: RoleUpdate) -> dict[str, Any]:
        if name not in cfg.roles:
            raise HTTPException(status_code=404, detail=f"no role {name!r}")
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if changes.get("route") and changes["route"] not in cfg.routes:
            raise HTTPException(
                status_code=422,
                detail=(f"no route named {changes['route']!r}. Configured routes: "
                        f"{', '.join(sorted(cfg.routes))}."))
        try:
            write_role(cfg, name, changes)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"role": cfg.role(name).model_dump(mode="json"), "written": str(config_target_path(cfg))}

    @app.post("/api/roles")
    def create_review_agent(body: NewReviewAgent) -> dict[str, Any]:
        if body.name in cfg.roles:
            raise HTTPException(status_code=422, detail=f"role {body.name!r} already exists")
        # A panel agent with no prompt file fails at the point of use, mid-build,
        # after the tokens have been spent. Write one now -- but never over one
        # that already exists. Removing an agent deliberately leaves its prompt
        # behind so you do not lose what you wrote; re-adding the same name must
        # not then destroy it.
        existing = prompt_path(cfg, body.name)
        if body.prompt.strip():
            write_role_prompt(cfg, body.name, body.prompt)
        elif not existing.exists():
            write_role_prompt(cfg, body.name, REVIEW_STARTER.format(name=body.name))
        try:
            write_role(cfg, body.name, {
                "model": body.model, "review": True, "enabled": True,
                "samples": max(1, body.samples),
            })
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"role": cfg.role(body.name).model_dump(mode="json")}

    @app.delete("/api/roles/{name}")
    def remove_review_agent(name: str) -> dict[str, Any]:
        try:
            delete_role(cfg, name)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"removed": name}

    @app.put("/api/roles/{name}/prompt")
    def put_prompt(name: str, body: PromptBody) -> dict[str, Any]:
        if name not in cfg.roles:
            raise HTTPException(status_code=404, detail=f"no role {name!r}")
        path = write_role_prompt(cfg, name, body.prompt)
        return {"written": str(path), "bytes": len(body.prompt)}
