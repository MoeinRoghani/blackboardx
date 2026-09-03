# Test an application

Every timed behaviour is observable without waiting, because the clock is
injected.

## Which store a test uses

| Test | Store |
| --- | --- |
| A unit test of your agents, rules, or timing | `InMemoryStore()` |
| A test that has to see the storage semantics a deployment has | `SqliteStore(":memory:")` |
| A test that closes a store and opens the record again | `SqliteStore(str(tmp_path / "board.sqlite3"))` |
| A test of your own `BoardStore` implementation | Your adapter, through the conformance suite |

`SqliteStore` takes its path and has no default. `":memory:"` is private to the
store that opened it, so a second `SqliteStore(":memory:")` in the same process
reads an empty board; a test that opens the same record twice gives both stores
a file.

Content crosses every store as JSON, the in-memory one included, so a test
cannot pass against content that a deployment would refuse.

## The manual clock

```python
from datetime import UTC, datetime, timedelta
from blackboard import InMemoryStore, ManualClock, Settled, create_model

clock = ManualClock(start=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
model = create_model(..., clock=clock, store=InMemoryStore())
```

`advance` moves time and fires every due call synchronously, on the calling
thread, in the order their instants fall. A call armed during an advance fires
inside it when its instant falls at or before the instant the advance moves to.

```python
clock.advance(timedelta(minutes=10))
assert model.control.outcome() == Settled()
```

`SystemClock` is the default and the only code in the library that reads the
operating system clock.

## A whole scenario on one thread

Acknowledge inside the callback, and the entire cycle runs inline, so a test
that drives a cycle this way needs no threads.

```python
def agent_cycle(notification):
    model.control.write("platform", "a finding", writer="ocp")
    model.control.ack(notification.notification_id, agent="ocp")


model.control.register_agent(
    Agent(name="ocp", notify=agent_cycle, subscribes_to={"signals"})
)
```

Name what wakes the agent. An agent registered without `subscribes_to` is woken
by every premise; no level wakes it, so a cycle driven by a level write never
runs.

Chained notifications are drained from a queue rather than the call stack, so
agents that wake each other do not grow the call stack. The wall clock is the
only bound the library puts on such an exchange, so a test that drives such an
exchange bounds it itself.

## One agent body, tested in process

`Control.as_agent(name)` returns the board as that agent sees it. It satisfies
`AgentBoard`, and so does the `BoardClient` that an agent deployed on its own
holds,
so a body written against that protocol is tested with no HTTP and no client.

```python
from blackboard import AgentBoard

LIMITS = RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=5))


def triage(board: AgentBoard, from_sequence: int) -> None:
    for signal in board.read_level("signals", from_sequence):
        board.write("findings", {"from": signal.sequence})


def test_the_agent_body_in_process():
    model = create_model(
        board_id="incident-3391",
        store=InMemoryStore(),
        regions=[Level("signals"), Level("findings"), Premise("severity")],
        premises={"severity": "high"},
        limits=LIMITS,
        clock=ManualClock(),
    )
    model.control.write("signals", "disk full", writer="watchdog")

    triage(model.control.as_agent("triage"), 0)

    assert [c.content for c in model.reader.read_level("findings")] == [{"from": 2}]
```

The finding is at sequence 2 rather than 1, because the opening value of
`severity` is a write and took sequence 1.

## Driving a batch window

```python
model = create_model(
    regions=[Premise("namespace", batch_window=timedelta(seconds=5))], ...
)
model.control.set_premise("namespace", ["ns1"], expected_version=1, writer="operator")
clock.advance(timedelta(seconds=2))
model.control.set_premise(
    "namespace", ["ns1", "ns2"], expected_version=2, writer="operator"
)

assert notifications == []                       # still inside the window
clock.advance(timedelta(seconds=3))
assert len(notifications) == 1                   # one notification, covering the window
```

The window opens at the first change that the region takes, which is the
opening value that `create_model` writes, so the one notification covers that
write and both premise
writes after it. A level carries a window on the same terms, so
`Level("signals", batch_window=timedelta(seconds=5))` is driven by the same two
advances.

## A run that opens over an existing record

`attach_model` opens a run over a board that a store already holds, so a test
of a
replacement replica needs no second process. Give the store before the restart
and the store after it one file, because a second `SqliteStore(":memory:")`
reads an empty board.

```python
def test_a_run_that_opens_over_an_existing_record(tmp_path):
    file = str(tmp_path / "board.sqlite3")
    regions = [Level("signals"), Level("findings"), Premise("severity")]

    first = SqliteStore(file)
    opened = create_model(
        board_id="incident-3391",
        store=first,
        regions=regions,
        premises={"severity": "high"},
        limits=LIMITS,
        clock=ManualClock(),
    )
    opened.control.write("signals", "disk full", writer="watchdog")
    opened.control.abort("the process is going away")
    first.close()

    second = SqliteStore(file)
    woken = []
    resumed = attach_model(
        board_id="incident-3391",
        store=second,
        regions=regions,
        agents=[Agent(name="triage", notify=woken.append, subscribes_to={"signals"})],
        limits=LIMITS,
        clock=ManualClock(),
    )

    assert resumed.reader.read_premise("severity").version == 1
    assert woken[0].from_sequence == 1
    assert resumed.control.write("findings", "x", writer="triage").sequence == 3

    resumed.control.abort("done")
    second.close()
```

The sequence continues from the record, and the agent is woken from sequence 1
because a run does not carry a cursor over from the record. `regions` is
checked against the record by
name and by kind, so a test that renames a region in `regions` fails at
`attach_model` rather than at the first write.

## Assert on the audit, not on timing

```python
from blackboard import WriteAccepted, WriteRejected

rejected = [e for e in model.control.read_audit() if isinstance(e, WriteRejected)]
assert [e.reason for e in rejected] == [
    "a duplicate of a contribution already on the board"
]
```

The audit records every event in the order it occurred.

## Testing your own adapter

`blackboard.conformance` is the suite that every store implementation owes, and
it ships with the package:

```
pip install 'blackboardx[conformance]'
```

Subclass `BoardConformance`, and give it a `store` fixture returning a fresh
store, and its fifty-eight cases run against your store. Subclass
`SharedStoreConformance` as well, with a `store` fixture of its own, for the
eleven cases about one store holding many boards.

```python
import pytest
from blackboard.conformance import BoardConformance, SharedStoreConformance

from myapp.storage import CassandraStore


class TestCassandraStore(BoardConformance):
    @pytest.fixture
    def store(self):
        return CassandraStore(session)


class TestCassandraHoldsManyBoards(SharedStoreConformance):
    @pytest.fixture
    def store(self):
        return CassandraStore(session)
```

That is how `InMemoryStore`, `SqliteStore`, `PostgresStore`, and `MongoStore`
are held to the same behaviour, with `PostgresStore` and `MongoStore` run
against real servers. They are held to that module rather than a copy of it.

Reading the rules and reimplementing them is not the same as running the suite.
A store that gives each region its own counter fails six cases here.

`ManualClock` and `SystemClock` both satisfy the `Clock` protocol, whose
`call_at` returns a `ScheduledCall` that the control component cancels when a
deadline moves. A clock of your own satisfies the same two methods, `now` and
`call_at`.
