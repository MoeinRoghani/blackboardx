"""An agent that comes back registers again rather than being refused."""

from datetime import UTC, datetime, timedelta

from blackboard import (
    Agent,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    Premise,
    RunLimits,
    Settled,
    create_model,
)

START = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))


def a_model(agents: list[Agent] | None = None, clock: ManualClock | None = None):  # type: ignore[no-untyped-def]
    return create_model(
        board_id="board-a",
        store=InMemoryStore(),
        regions=[Level("platform"), Premise("window")],
        premises={"window": "w"},
        agents=agents,
        limits=LIMITS,
        clock=clock or ManualClock(start=START),
    )


class TestRegisteringAgain:
    def test_a_second_registration_is_not_refused(self) -> None:
        first: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=first.append)])
        second: list[Notification] = []
        model.control.register_agent(Agent(name="ocp", notify=second.append))
        assert len(second) == 1

    def test_the_new_callback_is_the_one_reached(self) -> None:
        first: list[Notification] = []
        second: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=first.append)])
        model.control.register_agent(Agent(name="ocp", notify=second.append))
        before = len(first)
        model.control.set_premise("operator", "window", "w2", expected_version=1)
        assert len(first) == before, "the old callback is no longer reached"
        assert len(second) == 2

    def test_the_cursor_survives_so_nothing_already_seen_repeats(self) -> None:
        first: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=first.append)])
        model.control.ack("ocp", first[0].notification_id)
        model.control.set_premise("operator", "window", "w2", expected_version=1)
        model.control.ack("ocp", first[1].notification_id)

        second: list[Notification] = []
        model.control.register_agent(Agent(name="ocp", notify=second.append))
        # It acknowledged everything, so the notification it gets on returning
        # covers only what has happened since, which is nothing.
        (again,) = second
        assert again.from_sequence == 3

    def test_it_hears_about_what_it_missed_while_it_was_gone(self) -> None:
        first: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=first.append)])
        model.control.ack("ocp", first[0].notification_id)
        # It disappears here, and two changes land.
        model.control.set_premise("operator", "window", "w2", expected_version=1)
        model.control.set_premise("operator", "window", "w3", expected_version=2)

        second: list[Notification] = []
        model.control.register_agent(Agent(name="ocp", notify=second.append))
        (again,) = second
        assert again.from_sequence == 2
        assert again.to_sequence == 3

    def test_a_new_subscription_replaces_the_old_one(self) -> None:
        got: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=got.append)])
        model.control.register_agent(
            Agent(name="ocp", notify=got.append, subscribes_to=["platform"])
        )
        before = len(got)
        model.control.set_premise("operator", "window", "w2", expected_version=1)
        assert len(got) == before, "it no longer subscribes to the premise"
        model.control.write("other", "platform", "a finding")
        assert len(got) == before + 1

    def test_the_run_does_not_wait_on_a_notification_the_old_process_held(
        self,
    ) -> None:
        clock = ManualClock(start=START)
        got: list[Notification] = []
        model = a_model([Agent(name="ocp", notify=got.append)], clock=clock)
        # It never acknowledged, then it came back and acknowledged the one
        # it was given on returning.
        model.control.register_agent(Agent(name="ocp", notify=got.append))
        model.control.ack("ocp", got[-1].notification_id)
        clock.advance(LIMITS.idle)
        assert model.control.outcome() == Settled()
