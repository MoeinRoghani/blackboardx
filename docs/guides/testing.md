# Test an application

Every timed behaviour is observable without waiting, because the clock is injected.

## Which board a test uses

| Test | Board |
| --- | --- |
| A unit test of your agents, rules, or timing | `InMemoryBoard()` |
| A test that has to see the storage semantics a deployment has | `SqliteBoard()` |
| A test of your own `BoardStore` implementation | Your adapter, through the conformance suite |

Both are honest about content: it crosses every board as JSON, so a test cannot pass against content a deployment would refuse.

## The manual clock

```python
from datetime import UTC, datetime, timedelta
from blackboard import ManualClock

clock = ManualClock(start=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
model = create_model(..., clock=clock, board=InMemoryBoard())
```

`advance` moves time and fires every due call synchronously, on the calling thread, in due order. A call armed during an advance fires inside it when its instant falls at or before the target.

```python
clock.advance(timedelta(minutes=10))
assert model.control.outcome() == Settled()
```

`SystemClock` is the default and the only code in the library that reads the operating system clock.

## A whole scenario on one thread

Acknowledge inside the callback and the entire cycle runs inline, so no test needs threads.

```python
def agent_cycle(notification):
    model.control.write("ocp", "platform", "a finding")
    model.control.ack("ocp", notification.notification_id)


model.control.register_agent(Agent(name="ocp", notify=agent_cycle))
```

Chained wakes are drained from a queue rather than the call stack, so agents that wake each other do not grow it. Nothing in the library bounds such an exchange except the wall clock, so a test that drives one bounds it itself.

## Driving a batch window

```python
model = create_model(
    regions=[Register("namespace", batch_window=timedelta(seconds=5))], ...
)
model.control.set_register("operator", "namespace", ["ns1"], expected_version=1)
clock.advance(timedelta(seconds=2))
model.control.set_register("operator", "namespace", ["ns1", "ns2"], expected_version=2)

assert wakes == []                       # still inside the window
clock.advance(timedelta(seconds=3))
assert len(wakes) == 1                   # one wake covering both
```

## Assert on the audit, not on timing

```python
from blackboard import WriteAccepted, WriteRejected

rejected = [e for e in model.control.read_audit() if isinstance(e, WriteRejected)]
assert [e.reason for e in rejected] == [
    "a duplicate of a contribution already on the board"
]
```

The audit records every event in the order it occurred, which is a fact about what happened rather than about how fast it happened.

## Substituting the board

`create_model` takes a `board`, so a test can drive any implementation of `BoardStore` and assert the control component used it.
