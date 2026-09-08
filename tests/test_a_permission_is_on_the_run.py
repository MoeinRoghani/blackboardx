"""What an agent may write to is on the run, so every replica enforces it.

`writes_to` is recorded beside the agent's name and its subscriptions, which
is what lets a replica that never saw the declaration decide. Reading it from
the roster this process happens to hold makes the answer depend on which
replica took the request, and a permission that holds on one replica and not
another is worse than none.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    Premise,
    Rejected,
    RejectionCause,
    RunLimits,
    Written,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(seconds=30))


def a_board(store: Any, **overrides: Any) -> Any:
    settings: dict[str, Any] = {
        "board_id": "incident-1",
        "store": store,
        "regions": [Level("findings"), Level("signals"), Premise("severity")],
        "premises": {"severity": "unknown"},
        "limits": LIMITS,
    }
    settings.update(overrides)
    return create_model(**settings)


def declared(store: Any, **overrides: Any) -> Any:
    """Declares an agent through one replica and answers with another."""
    settings: dict[str, Any] = {"name": "reader", "notify": lambda n: None}
    settings.update(overrides)
    a_board(store).control.register_agent(Agent(**settings))
    return a_board(store)


class TestAReplicaThatDidNotDeclareTheAgent:
    def test_it_refuses_a_level_the_agent_may_not_write_to(self) -> None:
        store = InMemoryStore()
        elsewhere = declared(store, writes_to=["signals"])

        answer = elsewhere.control.write("findings", "oom", writer="reader")

        assert isinstance(answer, Rejected)
        assert answer.cause is RejectionCause.NOT_PERMITTED

    def test_it_refuses_every_level_where_the_agent_may_write_to_none(self) -> None:
        store = InMemoryStore()
        elsewhere = declared(store, writes_to=[])

        answer = elsewhere.control.write("findings", "oom", writer="reader")

        assert isinstance(answer, Rejected)
        assert answer.cause is RejectionCause.NOT_PERMITTED

    def test_the_refusal_leaves_nothing_on_the_board(self) -> None:
        store = InMemoryStore()
        elsewhere = declared(store, writes_to=[])

        elsewhere.control.write("findings", "oom", writer="reader")

        assert elsewhere.reader.read_level("findings") == []

    def test_it_permits_a_level_the_agent_declared(self) -> None:
        store = InMemoryStore()
        elsewhere = declared(store, writes_to=["findings"])

        answer = elsewhere.control.write("findings", "oom", writer="reader")

        assert isinstance(answer, Written)

    def test_both_replicas_answer_the_same_way(self) -> None:
        store = InMemoryStore()
        holding = a_board(store)
        holding.control.register_agent(
            Agent(name="reader", notify=lambda n: None, writes_to=[])
        )
        elsewhere = a_board(store)

        here = holding.control.write("findings", "oom", writer="reader")
        there = elsewhere.control.write("findings", "oom", writer="reader")

        assert isinstance(here, Rejected)
        assert isinstance(there, Rejected)
        assert here.cause is there.cause


class TestWhatStaysPermitted:
    def test_an_agent_that_declared_no_permission_writes_anywhere(self) -> None:
        store = InMemoryStore()
        elsewhere = declared(store)

        assert isinstance(
            elsewhere.control.write("findings", "oom", writer="reader"), Written
        )

    def test_a_name_nobody_declared_reaches_any_declared_level(self) -> None:
        """Documented: the permission is held against a name that was declared."""
        store = InMemoryStore()
        declared(store, writes_to=[])
        elsewhere = a_board(store)

        answer = elsewhere.control.write("findings", "oom", writer="nobody")

        assert isinstance(answer, Written)

    def test_a_premise_is_not_constrained_by_it(self) -> None:
        """`writes_to` names levels. A premise write is guarded by its version."""
        store = InMemoryStore()
        elsewhere = declared(store, writes_to=[])

        answer = elsewhere.control.set_premise(
            "severity", "high", expected_version=1, writer="reader"
        )

        assert isinstance(answer, Written)
