# End a run

Three things end a run, and the outcome it produces names which of the three it was.

## Silence

Most runs end this way, when nothing has happened for the idle limit.

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

A write of either kind pushes the deadline out, and so does a registration or an acknowledgment. Reads do not, so an agent polling the board cannot hold a run open.

Choose the idle limit by how long an agent's slowest step takes. Shorter than that and a run closes while an agent is still thinking; much longer and a finished run sits open.

## Requiring something of the result

A termination predicate is asked when the idle limit passes, and answering continue re-arms it.

```python
from blackboard import TerminationDecision


def both_levels_have_something(reader):
    if reader.read_level("platform") and reader.read_level("application"):
        return TerminationDecision.COMPLETE
    return TerminationDecision.CONTINUE


model = create_model(..., termination_predicate=both_levels_have_something)
```

Supplying none lets silence close the run on its own.

The predicate runs without the control component's lock, so a verdict is discarded if the board moved while it ran, and the next deadline asks again.

## The wall clock, and closing by hand

The wall clock is the hard stop, and it applies whatever the predicate says. A caller may also close a run outright.

```python
model.control.abort("the operator stopped the investigation")
```

## Reading the outcome

```python
outcome = model.control.wait_closed(timeout=timedelta(seconds=30))

match outcome:
    case Settled(unfinished=agents) if not agents:
        ...  # everyone finished
    case Settled(unfinished=agents):
        ...  # settled, but these never came back
    case WallClockExpired(unfinished=agents):
        ...  # ran out of time
    case Aborted(reason=reason):
        ...  # someone stopped it
    case None:
        ...  # still open when the timeout passed
```

The empty case takes a guard because `frozenset()` written as a pattern
matches every frozenset, empty or not, and would swallow the case under it.

Match the outcome rather than checking that it is not `None`, because `unfinished` carries a result the outcome alone does not. A region left empty because nobody examined it and a region left empty because an agent looked and found nothing are different findings, and `unfinished` is what separates them.

`Settled` and `WallClockExpired` name the agents still holding an unacknowledged notification. `Aborted` names none: `abort` closes the run and leaves `unfinished` empty, so a caller that stopped a run and wants to know who had not answered reads the audit, where each `NotificationDispatched` without a matching `NotificationAcknowledged` is one of them.

## Being told instead of asking

`wait_closed` blocks the calling thread. A caller that would rather be told
gives `create_model` or `attach_model` an `on_closed` callback.

```python
model = create_model(..., on_closed=record_the_outcome)
```

It is called once, with the same `RunOutcome` `wait_closed` returns, on
whichever thread closed the run: the caller's thread for `abort`, and the
clock's for the idle limit and the wall clock. An exception it raises is
suppressed, so a callback that fails does not reach whoever closed the run.

The outcome names no board, so a callback that serves several runs takes the
identifier from the call that opened the one it belongs to. [Serve a
blackboard](serving-a-blackboard.md) uses `on_closed` that way, to drop a
closed run from the registry the service reads.

## After it closes

Reads and the audit keep working, so the result stays available. A write to either kind of region comes back `Rejected` with the cause `RUN_CLOSED`. Registering an agent or declaring a region raises `RunClosedError`. [The run](../concepts/run.md#after-closing) explains why one returns and the other raises.

## The names

`TerminationPredicate` is the type of the callable `create_model` takes: it
receives a `BoardReader` and answers a `TerminationDecision`.

`on_closed`, on `create_model` and `attach_model`, is called once with the
`RunOutcome` when the run ends, on whichever thread ended it. It is how a
service holding many runs learns that one finished, where `outcome` asks and
`wait_closed` blocks.
