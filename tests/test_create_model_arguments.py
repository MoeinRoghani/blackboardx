"""The entry point reads what it is given once, and checks it before it writes.

Two failures these cover, both silent before: an iterable read twice is empty
the second time, and a configuration error found after the board exists leaves
a board nobody can replace.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from blackboard import (
    Agent,
    BlackboardError,
    DuplicateRegionError,
    InMemoryStore,
    Level,
    ManualClock,
    Premise,
    PremiseError,
    RunLimits,
    UndeclaredRegionError,
    Written,
    create_model,
)

LIMITS = RunLimits(wall_clock=timedelta(minutes=5), idle=timedelta(minutes=5))


def woken() -> tuple[list[Any], Any]:
    seen: list[Any] = []
    return seen, seen.append


class TestAnIterableIsReadOnce:
    def test_a_roster_given_as_a_generator_registers_every_agent(self) -> None:
        first, notify_first = woken()
        second, notify_second = woken()
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=[Level("signals"), Premise("window")],
            premises={"window": "w"},
            agents=(
                a
                for a in (
                    Agent(name="one", notify=notify_first),
                    Agent(name="two", notify=notify_second),
                )
            ),
            limits=LIMITS,
        )
        assert first != []
        assert second != []
        assert isinstance(
            model.control.write("signals", {"n": 1}, writer="one"), Written
        )

    def test_regions_given_as_a_generator_are_all_declared(self) -> None:
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=(r for r in (Level("signals"), Level("findings"))),
            premises={},
            agents=[],
            limits=LIMITS,
        )
        assert sorted(r.name for r in model.reader.read_regions()) == [
            "findings",
            "signals",
        ]

    def test_subscribes_to_given_as_a_generator_still_wakes_the_agent(self) -> None:
        seen, notify = woken()
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[
                Agent(name="src", notify=lambda n: None),
                Agent(
                    name="triage",
                    subscribes_to=(r for r in ("signals",)),
                    notify=notify,
                ),
            ],
            limits=LIMITS,
        )
        before = len(seen)
        model.control.write("signals", {"n": 1}, writer="src")
        assert len(seen) > before

    def test_writes_to_given_as_a_generator_still_permits_the_write(self) -> None:
        model = create_model(
            board_id="b",
            store=InMemoryStore(),
            regions=[Level("signals")],
            premises={},
            agents=[
                Agent(
                    name="triage",
                    writes_to=(r for r in ("signals",)),
                    notify=lambda n: None,
                )
            ],
            limits=LIMITS,
        )
        assert isinstance(
            model.control.write("signals", {"n": 1}, writer="triage"), Written
        )

    def test_a_declaration_reads_back_what_it_was_given(self) -> None:
        agent = Agent(name="a", notify=lambda n: None, subscribes_to=(x for x in "ab"))
        assert set(agent.subscribes_to or ()) == {"a", "b"}
        assert set(agent.subscribes_to or ()) == {"a", "b"}


class TestNothingIsWrittenUntilEverythingIsChecked:
    def store_after(self, **settings: Any) -> InMemoryStore:
        store = InMemoryStore()
        with pytest.raises(BlackboardError):
            create_model(
                board_id="b",
                store=store,
                limits=LIMITS,
                **settings,
            )
        return store

    def test_an_agent_naming_an_undeclared_region_leaves_no_board(self) -> None:
        store = self.store_after(
            regions=[Level("findings"), Premise("window")],
            premises={"window": "w"},
            agents=[
                Agent(name="triage", notify=lambda n: None),
                Agent(name="netops", subscribes_to={"typo"}, notify=lambda n: None),
            ],
        )
        assert store.read_regions("b") == []

    def test_the_corrected_call_then_succeeds(self) -> None:
        store = self.store_after(
            regions=[Level("findings")],
            premises={},
            agents=[
                Agent(name="netops", subscribes_to={"typo"}, notify=lambda n: None)
            ],
        )
        model = create_model(
            board_id="b",
            store=store,
            regions=[Level("findings")],
            premises={},
            agents=[
                Agent(name="netops", subscribes_to={"findings"}, notify=lambda n: None)
            ],
            limits=LIMITS,
        )
        assert [r.name for r in model.reader.read_regions()] == ["findings"]

    def test_premises_naming_an_undeclared_region_leaves_no_board(self) -> None:
        store = self.store_after(
            regions=[Level("findings")], premises={"window": "w"}, agents=[]
        )
        assert store.read_regions("b") == []

    def test_a_declared_premise_with_no_value_leaves_no_board(self) -> None:
        store = self.store_after(regions=[Premise("window")], premises={}, agents=[])
        assert store.read_regions("b") == []

    def test_a_region_declared_twice_leaves_no_board(self) -> None:
        store = InMemoryStore()
        with pytest.raises(DuplicateRegionError):
            create_model(
                board_id="b",
                store=store,
                regions=[Level("findings"), Level("findings")],
                premises={},
                agents=[],
                limits=LIMITS,
            )
        assert store.read_regions("b") == []

    def test_an_opening_value_json_cannot_carry_leaves_no_board(self) -> None:
        store = self.store_after(
            regions=[Premise("window")], premises={"window": object()}, agents=[]
        )
        assert store.read_regions("b") == []

    def test_a_failed_creation_arms_no_timer(self) -> None:
        clock = ManualClock()
        with pytest.raises(UndeclaredRegionError):
            create_model(
                board_id="b",
                store=InMemoryStore(),
                regions=[Level("findings")],
                premises={},
                agents=[
                    Agent(name="netops", subscribes_to={"typo"}, notify=lambda n: None)
                ],
                limits=LIMITS,
                clock=clock,
            )
        assert clock._armed == 0

    def test_the_error_names_every_offending_region_at_once(self) -> None:
        with pytest.raises(UndeclaredRegionError) as raised:
            create_model(
                board_id="b",
                store=InMemoryStore(),
                regions=[Level("findings")],
                premises={},
                agents=[
                    Agent(
                        name="netops",
                        subscribes_to={"typo", "alsowrong"},
                        notify=lambda n: None,
                    )
                ],
                limits=LIMITS,
            )
        said = str(raised.value)
        assert "typo" in said
        assert "alsowrong" in said
        assert "netops" in said

    def test_a_premise_mismatch_still_names_the_argument_to_change(self) -> None:
        with pytest.raises(PremiseError, match="premises"):
            create_model(
                board_id="b",
                store=InMemoryStore(),
                regions=[Premise("window")],
                premises={},
                agents=[],
                limits=LIMITS,
            )
