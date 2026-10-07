"""The orchestrator, assembled from one part per stretch of the run.

Each part is a mixin holding the methods of one phase; `FactoryBase` holds what
they all share -- construction, the store, the state, events and `_phase`. No
method is defined in two of them, so the order below decides nothing but the
order a reader meets them in.
"""

from __future__ import annotations

from .base import FactoryBase, ProgressCallback
from .schedule import ScheduleMixin
from .guidance import GuidanceMixin
from .intake import IntakeMixin
from .build import BuildMixin
from .cut import CutMixin
from .commit import CommitMixin
from .breaker import BreakerMixin
from .blind import BlindSuiteMixin
from .assess import AssessMixin
from .converge import ConvergeMixin
from .lanes import BuildLaneMixin
from .environment import EnvironmentMixin
from .verify import VerifyLaneMixin
from .context import ContextMixin
from .human import HumanMixin

__all__ = ["Factory", "ProgressCallback"]


class Factory(
    ScheduleMixin,
    GuidanceMixin,
    IntakeMixin,
    BuildMixin,
    CutMixin,
    CommitMixin,
    BreakerMixin,
    BlindSuiteMixin,
    AssessMixin,
    ConvergeMixin,
    BuildLaneMixin,
    EnvironmentMixin,
    VerifyLaneMixin,
    ContextMixin,
    HumanMixin,
    FactoryBase,
):
    """Plain code decides which phase runs next; models work inside a phase."""
