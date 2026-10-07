"""FastAPI: the API, the console, and the progress stream, in one process.

Two levels of gate, two levels of resource:

    projects   a repo, its checks, its environment     repo ready: approve the checks
      features one unit of work in that project        gate 1: freeze the spec
                                                       gate 2: rule on the packet

The console is static files served from the same app. There is no build step
and no second server to run.

The package is laid out by what each part serves: a module per group of routes,
and beside them the request bodies, the progress hub, the step readouts and the
guides' view. `create_app` builds one `Context` -- the configuration, the
services made from it, and the lookups the routes share -- and hands it to each
group's `register`, in the order the routes were always registered. FastAPI
tries a request against its routes in that order, so the order is behaviour,
not layout.
"""

from __future__ import annotations

import logging
import os
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI

from ..config import Config, ConfigError, load_config
from ..llm import LLM
from ..preview import Previews
from ..projects import ProjectRegistry
from ..routes import RouteMonitor
from . import (as_built, features, gate0, lifecycle, logs, preview, projects, proposal, readings, review,
               settings, setup, static, summary, traces, version)
from .bodies import (DISPOSITIONS, AdoptRecommendationBody, AnswersBody, CheckNameBody, CheckRunsBody,
                     CleanupPick, CorrectionBody, DiagnosisFixBody, EditorUpdate, FamilyBody, FlagBody,
                     GateDeclineBody, GateNameBody, GuideDraftBody, GuideOfferBody, NewCodeBody,
                     NewFeature, NewProject, NewReviewAgent, ProjectUpdate, PromptBody,
                     ProposedCheckBody, ProviderUpdate, RecommendationBody, ResurveyRuling, RetagBody,
                     RoleUpdate, RulingBody, ScaffoldBody, SkillsDirBody, VerdictBody)
from .context import CONSOLE_DIR, ROOT, Context
from .guides_view import guide_contradictions, guide_offers, guide_view
from .hub import ProgressHub
from .lifespan import lifespan_for
from .logs import INLINE_LOG_KINDS
from .readouts import READOUT_CAP, READOUT_KINDS, readouts_for, step_readout

__all__ = [
    "AdoptRecommendationBody", "AnswersBody", "CONSOLE_DIR", "CheckNameBody", "CheckRunsBody",
    "CleanupPick", "Context", "CorrectionBody", "DISPOSITIONS", "DiagnosisFixBody", "EditorUpdate",
    "FamilyBody", "FlagBody", "GateDeclineBody", "GateNameBody", "GuideDraftBody", "GuideOfferBody",
    "INLINE_LOG_KINDS", "NewCodeBody", "NewFeature", "NewProject", "NewReviewAgent", "ProgressHub",
    "ProjectUpdate", "PromptBody", "ProposedCheckBody", "ProviderUpdate", "READOUT_CAP",
    "READOUT_KINDS", "ROOT", "RecommendationBody", "ResurveyRuling", "RetagBody", "RoleUpdate",
    "RulingBody", "ScaffoldBody", "SkillsDirBody", "VerdictBody", "app", "create_app",
    "guide_contradictions", "guide_offers", "guide_view", "readouts_for", "resolve_config",
    "step_readout",
]


def resolve_config(path: str | Path | None = None) -> Config:
    candidates = [path] if path else []
    candidates += [os.environ.get("FACTORY_CONFIG"), ROOT / "factory.yaml", ROOT / "factory.example.yaml"]
    errors = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return load_config(candidate)
        except ConfigError as exc:
            errors.append(str(exc))
    raise ConfigError("no usable config found:\n" + "\n".join(errors))


def _log_to_stderr() -> None:
    """Somewhere for Fabrika's own messages to go.

    uvicorn configures its own loggers and nobody else's, so a failure in a
    background run had nowhere to be said but a bare traceback. Everything under
    `factory.` now logs through one handler with a time and a source on every
    line. Once, however many apps a process makes.
    """
    root = logging.getLogger("factory")
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app(config: Config | None = None) -> FastAPI:
    _log_to_stderr()
    cfg = config or resolve_config()
    hub = ProgressHub()
    registry = ProjectRegistry(cfg.evidence_path)
    #: The running apps people have opened at review. Held here, so they end
    #: with this process; the boot sweep (lifespan.py) removes what a killed one left.
    previews = Previews(cfg, hub.publish)
    llm = LLM(cfg)
    routes = RouteMonitor(cfg, llm)
    ctx = Context(cfg=cfg, hub=hub, registry=registry, previews=previews, llm=llm, routes=routes)

    # The number pyproject.toml declares, so the API page and the package
    # cannot disagree. Fabrika is installed editable; a bare checkout says so.
    try:
        release = metadata.version("fabrika")
    except metadata.PackageNotFoundError:
        release = "unknown (not installed)"
    app = FastAPI(title="Fabrika", version=release, lifespan=lifespan_for(ctx))
    app.state.config = cfg
    app.state.registry = registry
    app.state.hub = hub
    app.state.previews = previews
    app.state.llm = llm

    # In the order they were always registered, which is the order FastAPI
    # tries them in. A group moved up or down this list can change which route
    # answers a request; nothing about the groups themselves would show it.
    # It is also why some groups are small: `/api/version` was registered
    # between asking for a proposal and reading it, and still is.
    projects.register(app, ctx)
    gate0.register(app, ctx)
    version.register(app, ctx)
    proposal.register(app, ctx)
    summary.register(app, ctx)
    readings.register(app, ctx)
    as_built.register(app, ctx)
    settings.register(app, ctx)
    setup.register(app, ctx)
    features.register(app, ctx)
    preview.register(app, ctx)
    logs.register(app, ctx)
    lifecycle.register(app, ctx)
    traces.register(app, ctx)
    review.register(app, ctx)
    static.register(app, ctx)

    return app


app = create_app() if os.environ.get("FACTORY_NO_AUTOAPP") != "1" else None
