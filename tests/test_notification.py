"""Registers are the only regions that notify; every agent is subscribed to all."""

from datetime import UTC, datetime, timedelta

import pytest

from blackboard import (
    Agent,
    BoardReader,
    DuplicateAgentError,
    InMemoryStore,
    Level,
    ManualClock,
    Notification,
    NotificationAcknowledged,
    NotificationDispatched,
    NotificationId,
    Premise,
    RunLimits,
    ScheduledCall,
    TerminationDecision,
    UnknownNotificationError,
    UnsetPremiseError,
    Written,
)
from blackboard._control import Control

START = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=30))


def keep_open(reader: BoardReader) -> TerminationDecision:
    return TerminationDecision.CONTINUE


class Recorder:
    """Collects the notifications delivered to one agent."""

    def __init__(self) -> None:
        self.received: list[Notification] = []

    def __call__(self, notification: Notification) -> None:
        self.received.append(notification)


def agent(name: str, recorder: Recorder) -> Agent:
    return Agent(
        name=name,
        notify=recorder,
    )


def make_control(clock: ManualClock, *agents: Agent) -> Control:
    control = Control(
        regions=[
            Level("application"),
            Premise("window"),
            Premise("namespace", batch_window=timedelta(seconds=5)),
        ],
        admission_rule=None,
        termination_predicate=keep_open,
        limits=LIMITS,
        clock=clock,
        board_id="test-board",
        store=InMemoryStore(),
    )
    for declared in agents:
        control.register_agent(declared)
    return control


class TestDeclarations:
    def test_a_duplicate_agent_name_is_refused(self) -> None:
        clock = ManualClock(start=START)
        control = make_control(clock, agent("ocp", Recorder()))
        with pytest.raises(DuplicateAgentError):
            control.register_agent(agent("ocp", Recorder()))


class TestZeroWindowDispatch:
    def test_a_register_write_notifies_every_agent_at_once(self) -> None:
        clock = ManualClock(start=START)
        first, second = Recorder(), Recorder()
        control = make_control(clock, agent("ocp", first), agent("git", second))
        control.set_premise("operator", "window", "w", expected_version=0)
        expected = [
            Notification(
                board_id="test-board",
                notification_id=NotificationId(nid),
                agent=name,
                from_sequence=1,
                to_sequence=1,
                regions=frozenset({"window"}),
            )
            for nid, name in ((1, "ocp"), (2, "git"))
        ]
        assert first.received == [expected[0]]
        assert second.received == [expected[1]]

    def test_a_level_write_notifies_no_one(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.write("git", "application", "content")
        assert recorder.received == []

    def test_the_writer_is_not_notified_of_its_own_change(self) -> None:
        clock = ManualClock(start=START)
        writer, other = Recorder(), Recorder()
        control = make_control(clock, agent("ocp", writer), agent("git", other))
        control.set_premise("ocp", "window", "w", expected_version=0)
        assert writer.received == []
        assert len(other.received) == 1

    def test_dispatch_is_audited_before_the_callback_runs(self) -> None:
        clock = ManualClock(start=START)
        seen_during_callback: list[bool] = []
        control_holder: list[Control] = []

        def looks_at_audit(notification: Notification) -> None:
            audited = any(
                isinstance(event, NotificationDispatched)
                and event.notification == notification
                for event in control_holder[0].read_audit()
            )
            seen_during_callback.append(audited)

        control = make_control(
            clock,
            Agent(
                name="ocp",
                notify=looks_at_audit,
            ),
        )
        control_holder.append(control)
        control.set_premise("operator", "window", "w", expected_version=0)
        assert seen_during_callback == [True]

    def test_a_callback_may_run_its_whole_cycle_inline(self) -> None:
        clock = ManualClock(start=START)
        control_holder: list[Control] = []

        def full_cycle(notification: Notification) -> None:
            control = control_holder[0]
            control.write("ocp", "application", "seen")
            control.ack("ocp", notification.notification_id)

        control = make_control(
            clock,
            Agent(
                name="ocp",
                notify=full_cycle,
            ),
        )
        control_holder.append(control)
        control.set_premise("operator", "window", "w", expected_version=0)
        (contribution,) = control.reader.read_level("application")
        assert contribution.content == "seen"
        assert any(
            isinstance(event, NotificationAcknowledged)
            for event in control.read_audit()
        )


class TestBatchWindows:
    def test_changes_inside_one_window_collapse_into_one_notification(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "namespace", ["ns1"], expected_version=0)
        clock.advance(timedelta(seconds=2))
        control.set_premise("operator", "namespace", ["ns1", "ns2"], expected_version=1)
        assert recorder.received == []
        clock.advance(timedelta(seconds=3))
        (notification,) = recorder.received
        assert notification.regions == frozenset({"namespace"})
        assert notification.from_sequence == 1
        assert notification.to_sequence == 2

    def test_a_zero_window_change_sweeps_a_pending_windowed_change(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "namespace", ["ns1"], expected_version=0)
        assert recorder.received == []
        control.set_premise("operator", "window", "w", expected_version=0)
        (notification,) = recorder.received
        assert notification.regions == frozenset({"namespace", "window"})
        assert notification.from_sequence == 1
        assert notification.to_sequence == 2
        clock.advance(timedelta(seconds=10))
        assert len(recorder.received) == 1

    def test_the_window_opens_at_the_first_change(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "namespace", ["ns1"], expected_version=0)
        clock.advance(timedelta(seconds=4))
        control.set_premise("operator", "namespace", ["ns1", "ns2"], expected_version=1)
        clock.advance(timedelta(seconds=1))
        assert len(recorder.received) == 1


class TestAcknowledgment:
    def test_ack_advances_the_cursor(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "window", "w1", expected_version=0)
        control.ack("ocp", recorder.received[0].notification_id)
        control.set_premise("operator", "window", "w2", expected_version=1)
        assert recorder.received[1].from_sequence == 2

    def test_an_unacknowledged_notification_leaves_the_cursor_in_place(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "window", "w1", expected_version=0)
        control.set_premise("operator", "window", "w2", expected_version=1)
        assert recorder.received[1].from_sequence == 1

    def test_acknowledging_twice_records_it_once(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "window", "w", expected_version=0)
        first = recorder.received[0].notification_id
        control.ack("ocp", first)
        control.ack("ocp", first)
        acknowledged = [
            e for e in control.read_audit() if isinstance(e, NotificationAcknowledged)
        ]
        assert len(acknowledged) == 1

    def test_a_repeated_ack_does_not_move_the_cursor_again(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.set_premise("operator", "window", "w1", expected_version=0)
        first = recorder.received[0].notification_id
        control.ack("ocp", first)
        control.ack("ocp", first)
        control.set_premise("operator", "window", "w2", expected_version=1)
        assert recorder.received[1].from_sequence == 2

    def test_a_fabricated_notification_id_raises(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        with pytest.raises(UnknownNotificationError):
            control.ack("ocp", 99)  # type: ignore[arg-type]  # a raw int stands in for a fabricated id


class TestMidRunRegistration:
    def test_a_mid_run_agent_is_woken_with_everything_already_written(self) -> None:
        clock = ManualClock(start=START)
        early, late = Recorder(), Recorder()
        control = make_control(clock, agent("early", early))
        control.set_premise("operator", "window", "w1", expected_version=0)
        control.register_agent(agent("late", late))
        assert len(late.received) == 1
        assert late.received[0].from_sequence == 1
        assert late.received[0].to_sequence == 1
        control.set_premise("operator", "window", "w2", expected_version=1)
        assert len(late.received) == 2
        assert late.received[1].to_sequence == 2

    def test_a_register_declared_mid_run_notifies_from_declaration(self) -> None:
        clock = ManualClock(start=START)
        recorder = Recorder()
        control = make_control(clock, agent("ocp", recorder))
        control.declare(Premise("trigger"))
        control.set_premise("operator", "trigger", "alert", expected_version=0)
        assert len(recorder.received) == 1
        assert recorder.received[0].regions == frozenset({"trigger"})
        assert recorder.received[0].from_sequence == 1
        assert recorder.received[0].to_sequence == 1


class _NoCancelHandle:
    def cancel(self) -> None:
        """A timer whose call has started ignores cancellation."""


class TimerFaithfulClock:
    """A manual clock whose armed calls, like ``threading.Timer``, ignore cancel."""

    def __init__(self, start: datetime) -> None:
        self._inner = ManualClock(start=start)

    def now(self) -> datetime:
        return self._inner.now()

    def call_at(self, when: datetime, call: object) -> ScheduledCall:
        self._inner.call_at(when, call)  # type: ignore[arg-type]  # the inner clock takes the same callable
        return _NoCancelHandle()

    def advance(self, delta: timedelta) -> None:
        self._inner.advance(delta)


class TestStaleTimerCalls:
    def test_a_swept_window_call_does_not_dispatch_the_next_batch_early(self) -> None:
        clock = TimerFaithfulClock(START)
        control = Control(
            regions=[
                Premise("window"),
                Premise("namespace", batch_window=timedelta(seconds=5)),
            ],
            admission_rule=None,
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=clock,
            board_id="test-board",
            store=InMemoryStore(),
        )
        recorder = Recorder()
        control.register_agent(
            Agent(
                name="ocp",
                notify=recorder,
            )
        )
        control.set_premise("operator", "namespace", ["ns1"], expected_version=0)
        clock.advance(timedelta(seconds=1))
        control.set_premise("operator", "window", "w", expected_version=0)
        assert len(recorder.received) == 1
        clock.advance(timedelta(seconds=1))
        control.set_premise("operator", "namespace", ["ns1", "ns2"], expected_version=1)
        clock.advance(timedelta(seconds=3))
        assert len(recorder.received) == 1
        clock.advance(timedelta(seconds=2))
        assert len(recorder.received) == 2
        assert recorder.received[1].regions == frozenset({"namespace"})


class TestDeliveryFailure:
    def test_a_raising_callback_blocks_neither_the_batch_nor_the_writer(self) -> None:
        clock = ManualClock(start=START)
        delivered = Recorder()

        def explode(notification: Notification) -> None:
            raise RuntimeError("the agent process is gone")

        control = make_control(
            clock,
            Agent(
                name="broken",
                notify=explode,
            ),
            agent("ocp", delivered),
        )
        result = control.set_premise("operator", "window", "w", expected_version=0)
        assert isinstance(result, Written)
        assert len(delivered.received) == 1
        # The raising agent never acknowledges, so it is still holding its
        # notification when the run is asked who did not finish.
        assert control.write("ocp", "application", "unaffected") == Written(sequence=2)


class TestChainedWakes:
    def test_chained_inline_notifications_do_not_grow_the_stack(self) -> None:
        clock = ManualClock(start=START)
        control_holder: list[Control] = []
        counts = {"a": 0, "b": 0}

        def make_notify(me: str, target: str) -> object:
            def notify(notification: Notification) -> None:
                control = control_holder[0]
                counts[me] += 1
                # The agents bound the exchange themselves. Nothing in
                # the library stops a chain except the wall clock, and the
                # wall clock is not what this measures.
                if counts[me] < 300:
                    try:
                        version = control.reader.read_premise(target).version
                    except UnsetPremiseError:
                        version = 0
                    control.set_premise(
                        me, target, counts[me], expected_version=version
                    )
                control.ack(me, notification.notification_id)

            return notify

        control = Control(
            regions=[Premise("ra"), Premise("rb")],
            admission_rule=None,
            termination_predicate=keep_open,
            limits=LIMITS,
            clock=clock,
            board_id="test-board",
            store=InMemoryStore(),
        )
        control_holder.append(control)
        for name, target in (("a", "rb"), ("b", "ra")):
            control.register_agent(
                Agent(
                    name=name,
                    notify=make_notify(name, target),  # type: ignore[arg-type]  # the factory returns the callback type
                )
            )
        control.set_premise("operator", "ra", 0, expected_version=0)
        assert counts == {"a": 300, "b": 300}
