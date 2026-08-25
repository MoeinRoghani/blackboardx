"""A skeletal blackboard system.

The library supplies the board, the shared structure through which
independent agents contribute to one result, and the control component,
which determines which agents are notified of a change, whether a proposed
write is admitted, whether budgets hold, and when the run has finished. An
application creates a model by supplying its regions, their opening
premise values, an admission rule, a termination predicate, and limits.
Agents register themselves into it. The public surface is
the set of names in ``__all__``; every other name is internal.
"""

import warnings
from typing import TYPE_CHECKING, Any

from blackboard._board import (
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    DuplicateRegionError,
    InMemoryBoard,
    Level,
    Premise,
    PremiseState,
    RegionKindError,
    UndeclaredRegionError,
    UnsetPremiseError,
    Written,
)
from blackboard._clock import Clock, ManualClock, ScheduledCall, SystemClock
from blackboard._control import (
    Aborted,
    Accept,
    AdmissionRule,
    Agent,
    AuditEvent,
    BoardReader,
    BoardStore,
    Control,
    DuplicateAgentError,
    Notification,
    NotificationAcknowledged,
    NotificationDispatched,
    NotificationId,
    PremiseError,
    PremiseOpened,
    ProposedContribution,
    ProposedPremiseWrite,
    ProposedWrite,
    Reject,
    Rejected,
    RejectionCause,
    RunClosed,
    RunClosedError,
    RunLimits,
    RunOutcome,
    Settled,
    TerminationDecision,
    TerminationPredicate,
    UnknownNotificationError,
    WallClockExpired,
    WriteAccepted,
    WriteRejected,
)
from blackboard._model import Model, create_model
from blackboard._sqlite import SqliteBoard

if TYPE_CHECKING:
    from blackboard._board import Written as Accepted
    from blackboard._control import RunLimits as RunBudgets
    from blackboard._mongodb import MongoBoard
    from blackboard._postgres import PostgresBoard

__all__ = [
    "Aborted",
    "Accept",
    "Accepted",
    "AdmissionRule",
    "Agent",
    "AuditEvent",
    "BlackboardError",
    "BoardChange",
    "BoardReader",
    "BoardStore",
    "Clock",
    "Conflict",
    "Contribution",
    "Control",
    "DuplicateAgentError",
    "DuplicateRegionError",
    "InMemoryBoard",
    "Level",
    "ManualClock",
    "Model",
    "MongoBoard",
    "Notification",
    "NotificationAcknowledged",
    "NotificationDispatched",
    "NotificationId",
    "PostgresBoard",
    "Premise",
    "PremiseError",
    "PremiseOpened",
    "PremiseState",
    "ProposedContribution",
    "ProposedPremiseWrite",
    "ProposedRegisterWrite",
    "ProposedWrite",
    "RegionKindError",
    "Register",
    "RegisterSeeded",
    "RegisterState",
    "Reject",
    "Rejected",
    "RejectionCause",
    "RunBudgets",
    "RunClosed",
    "RunClosedError",
    "RunLimits",
    "RunOutcome",
    "ScheduledCall",
    "SeedError",
    "Settled",
    "SqliteBoard",
    "SystemClock",
    "TerminationDecision",
    "TerminationPredicate",
    "UndeclaredRegionError",
    "UnknownNotificationError",
    "UnsetPremiseError",
    "UnsetRegisterError",
    "WallClockExpired",
    "WriteAccepted",
    "WriteRejected",
    "Written",
    "create_model",
]


# A replaced name stays importable for one release. It resolves to its
# replacement, so equality and isinstance keep working across the change.
_RENAMED = {
    "Accepted": (
        "Written",
        "a write that landed reports Written, whose version is absent on a level write",
    ),
    "RunBudgets": (
        "RunLimits",
        "a run has two limits, both durations, and nothing countable is consumed",
    ),
    "Register": (
        "Premise",
        "register belongs to computer architecture, and the region holds what "
        "the work is given",
    ),
    "RegisterState": ("PremiseState", "the region is a Premise"),
    "UnsetRegisterError": ("UnsetPremiseError", "the region is a Premise"),
    "ProposedRegisterWrite": ("ProposedPremiseWrite", "the region is a Premise"),
    "RegisterSeeded": (
        "PremiseOpened",
        "a premise receives an opening value rather than being seeded",
    ),
    "SeedError": ("PremiseError", "the opening values are premises"),
}
_REMOVED_IN = "0.6.0"

_EXTRAS = {
    "MongoBoard": ("blackboard._mongodb", "mongodb"),
    "PostgresBoard": ("blackboard._postgres", "postgres"),
}


def __getattr__(name: str) -> Any:
    """Imports a board that needs an extra, and says which one when it is absent.

    Naming these here rather than importing them at the top keeps the base
    install free of database drivers, while leaving one import path.
    """
    replacement = _RENAMED.get(name)
    if replacement is not None:
        new, because = replacement
        warnings.warn(
            f"{name} is renamed {new}, and {name} is removed in "
            f"{_REMOVED_IN}: {because}.",
            DeprecationWarning,
            stacklevel=2,
        )
        return globals()[new]
    entry = _EXTRAS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, extra = entry
    from importlib import import_module

    try:
        module = import_module(module_name)
    except ImportError as absent:  # pragma: no cover - exercised by a test
        raise ImportError(
            f"{name} needs the {extra!r} extra: pip install 'blackboardx[{extra}]'"
        ) from absent
    return getattr(module, name)
