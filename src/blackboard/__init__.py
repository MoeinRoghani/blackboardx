"""A skeletal blackboard system.

The library supplies the board, the shared structure through which
independent agents contribute to one result, and the control component,
which determines which agents are notified of a change, which proposed writes are
admitted, when the run's limits still hold, and when the run has finished. An
application creates a model by supplying its regions,
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
    AgentBoard,
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
    reader_for,
)
from blackboard._model import Model, attach_model, create_model
from blackboard._run import (
    Acknowledged,
    Closure,
    Dispatched,
    InMemoryRunStore,
    RegisteredAgent,
    RunStore,
    UnknownRunError,
    sweep,
)
from blackboard._schema import SCHEMA_VERSION, SchemaVersionError
from blackboard._sqlite import SqliteStore

if TYPE_CHECKING:
    from blackboard._mongodb import MongoStore
    from blackboard._postgres import PostgresRunStore, PostgresStore

__all__ = [
    "SCHEMA_VERSION",
    "Aborted",
    "Accept",
    "Acknowledged",
    "AdmissionRule",
    "Agent",
    "AgentBoard",
    "AuditEvent",
    "BlackboardError",
    "BoardChange",
    "BoardReader",
    "BoardStore",
    "Clock",
    "Closure",
    "Conflict",
    "Contribution",
    "Control",
    "Deleted",
    "Dispatched",
    "DuplicateAgentError",
    "DuplicateRegionError",
    "IdempotencyKeyError",
    "InMemoryRunStore",
    "InMemoryStore",
    "Level",
    "ManualClock",
    "Model",
    "MongoStore",
    "Notification",
    "NotificationAcknowledged",
    "NotificationDispatched",
    "NotificationId",
    "PostgresRunStore",
    "PostgresStore",
    "Premise",
    "PremiseError",
    "PremiseOpened",
    "PremiseState",
    "ProposedContribution",
    "ProposedPremiseWrite",
    "ProposedWrite",
    "RegionKindError",
    "RegisteredAgent",
    "Reject",
    "Rejected",
    "RejectionCause",
    "RunClosed",
    "RunClosedError",
    "RunLimits",
    "RunOutcome",
    "RunStore",
    "ScheduledCall",
    "SchemaVersionError",
    "Settled",
    "SqliteStore",
    "SystemClock",
    "TerminationDecision",
    "TerminationPredicate",
    "UndeclaredRegionError",
    "UnknownNotificationError",
    "UnknownRunError",
    "UnsetPremiseError",
    "WallClockExpired",
    "WriteAccepted",
    "WriteRejected",
    "Written",
    "attach_model",
    "create_model",
    "reader_for",
    "sweep",
]


_EXTRAS = {
    "MongoStore": ("blackboard._mongodb", "mongodb"),
    "PostgresRunStore": ("blackboard._postgres", "postgres"),
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
