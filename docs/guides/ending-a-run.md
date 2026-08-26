# End a run

Three things end a run, and each produces an outcome that names the agents still holding an unacknowledged notification.

## Silence

Most runs end this way, when nothing has happened for the idle limit.

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

Every write, premise write, registration and acknowledgment pushes the deadline out. Reads do not, so an agent polling the board cannot hold a run open.

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
    case Settled(unfinished=frozenset()):
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

Match the outcome rather than checking that it is not `None`, because `unfinished` carries a result the outcome alone does not. A region left empty because nobody examined it and a region left empty because an agent looked and found nothing are different findings, and `unfinished` is what separates them.

## After it closes

Reads and the audit keep working, so the result stays available. A write to either kind of region comes back `Rejected` with the cause `RUN_CLOSED`. Registering an agent or declaring a region raises `RunClosedError`. [The run](../concepts/run.md#after-closing) explains why one returns and the other raises.
