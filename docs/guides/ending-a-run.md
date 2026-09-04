# End a run

Three things end a run, and the outcome the run produces names which of the
three ended it.

## Silence

A run ends on silence when nothing has happened for the idle limit.

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

A write to a level or a premise pushes the deadline out, and so does a
registration or an acknowledgment. Reads do not, so an agent polling the board
cannot hold a run open.

Choose the idle limit by how long an agent's slowest step takes. If it is
shorter than that, a run closes while an agent is still thinking; if it is much
longer, a finished run sits open.

## Requiring something of the result

A termination predicate is asked when the idle limit passes, and answering
`CONTINUE` re-arms the idle limit.

```python
from blackboard import TerminationDecision


def both_levels_have_something(reader):
    if reader.read_level("platform") and reader.read_level("application"):
        return TerminationDecision.COMPLETE
    return TerminationDecision.CONTINUE


model = create_model(..., termination_predicate=both_levels_have_something)
```

Supplying no predicate lets silence close the run on its own.

The predicate runs without the control component's lock, so its decision is
discarded if the board moved while it ran, and the predicate is asked again at
the next deadline.

## The wall clock

The wall clock closes the run whatever the predicate says, and it is the only
limit that ends a run that never goes quiet.

A run goes quiet when nothing happens on it, and agents that answer each other
never let that happen. A write by one wakes the other, whose write wakes the
first, and each write pushes the idle deadline out again. Two agents that each
take twenty seconds, under an idle limit of thirty, leave that deadline
permanently out of reach.

```python
RunLimits(wall_clock=timedelta(seconds=100), idle=timedelta(seconds=30))
# five rounds of answering each other, then:
# WallClockExpired(unfinished=frozenset({'a', 'b'}))
```

A termination predicate does not help here. It is asked when the idle deadline
passes, and in a run that never goes quiet that deadline never passes, so the
predicate is never asked at all.

Choose the wall clock by how long the work is allowed to take, and not from the
idle limit. The two guard different failures. A run that closes while an agent
is still thinking is the failure the idle limit is sized against; a run that
never closes at all is the failure the wall clock is sized against, and no
value of the idle limit prevents it.

## Closing by hand

A caller may close a run outright.

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
matches every frozenset, including a non-empty one, and would swallow the case
under it.

Match the outcome rather than checking that it is not `None`, because
`unfinished` carries a result that the outcome alone does not carry. A region
left empty because nobody examined it and a region left empty because an agent
looked and found nothing are different findings.

`Settled` and `WallClockExpired` name the agents still holding an
unacknowledged notification. `Aborted` names none: `abort` closes the run and
leaves `unfinished` empty, so a caller that stopped a run and wants to know who
had not answered reads the audit, where each `NotificationDispatched` without a
matching `NotificationAcknowledged` names one of those agents.

## Being told instead of asking

`wait_closed` blocks the calling thread. A caller that would rather be told
gives `create_model` or `attach_model` an `on_closed` callback.

```python
model = create_model(..., on_closed=record_the_outcome)
```

It is called once, with the same `RunOutcome` that `wait_closed` returns, on
whichever thread closed the run: the caller's thread for `abort`, and the
clock's for the idle limit and the wall clock. An exception it raises is
suppressed, so a callback that fails does not reach whoever closed the run.

The outcome names no board, so a callback that serves several runs takes the
identifier from the call that opened the run it belongs to. [Serve a
blackboard](serving-a-blackboard.md) uses `on_closed` that way, to drop a
closed run from the runs that the service reads.

## After it closes

Reads and the audit keep working, so the result stays available. A write to a
level or a premise comes back `Rejected` with the cause `RUN_CLOSED`.
Registering an agent or declaring a region raises `RunClosedError`. [The
run](../concepts/run.md#after-closing) explains why a write returns and a
registration raises.

## The names

`TerminationPredicate` is the type of the callable that `create_model` takes:
it
receives a `BoardReader` and answers a `TerminationDecision`.

`on_closed`, on `create_model` and `attach_model`, is called once with the
`RunOutcome` when the run ends, on whichever thread ended it. It is how a
service holding many runs learns that one finished, where `outcome` answers
when it is asked and `wait_closed` waits for the run to close.
