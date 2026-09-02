"""A skeletal blackboard system.

The library supplies the board, the shared structure through which
independent agents contribute to one result, and the control component,
which determines which agents are notified of a change, whether a proposed
write is admitted, whether the run's limits still hold, and when the run
has finished. An application creates a model by supplying its regions,
their opening premise values, the agents the run starts with, an admission
rule, a termination predicate, and limits. The public surface is the set of
names in ``__all__``; every other name is internal.
"""

from typing import TYPE_CHECKING, Any

from blackboard._board import (
    BlackboardError,
    BoardChange,
    Conflict,
    Contribution,
    Deleted,
    DuplicateRegionError,
    IdempotencyKeyError,
    InMemoryStore,
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
from blackboard._schema import SCHEMA_VERSION, SchemaVersionError
from blackboard._sqlite import SqliteStore

if TYPE_CHECKING:
    from blackboard._mongodb import MongoStore
    from blackboard._postgres import PostgresStore

__all__ = [
    "SCHEMA_VERSION",
    "Aborted",
    "Accept",
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
    "Deleted",
    "DuplicateAgentError",
    "DuplicateRegionError",
    "IdempotencyKeyError",
    "InMemoryStore",
    "Level",
    "ManualClock",
    "Model",
    "MongoStore",
    "Notification",
    "NotificationAcknowledged",
    "NotificationDispatched",
    "NotificationId",
    "PostgresStore",
    "Premise",
    "PremiseError",
    "PremiseOpened",
    "PremiseState",
    "ProposedContribution",
    "ProposedPremiseWrite",
    "ProposedWrite",
    "RegionKindError",
    "Reject",
    "Rejected",
    "RejectionCause",
    "RunClosed",
    "RunClosedError",
    "RunLimits",
    "RunOutcome",
    "ScheduledCall",
    "SchemaVersionError",
    "Settled",
    "SqliteStore",
    "SystemClock",
    "TerminationDecision",
    "TerminationPredicate",
    "UndeclaredRegionError",
    "UnknownNotificationError",
    "UnsetPremiseError",
    "WallClockExpired",
    "WriteAccepted",
    "WriteRejected",
    "Written",
    "create_model",
]


_EXTRAS = {
    "MongoStore": ("blackboard._mongodb", "mongodb"),
    "PostgresStore": ("blackboard._postgres", "postgres"),
}


def __getattr__(name: str) -> Any:
    """Imports a board that needs an extra, and says which one when it is absent.

    Naming these here rather than importing them at the top keeps the base
    install free of database drivers, while leaving one import path.
    """
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
