# The run

A model is one run. It opens when it is created, and it ends in one of three states.

## Creating one

Five things configure a model, and a sixth says where the record is kept.

```python
model = create_model(
    regions=[...],  # 1. the named parts of the board
    premises={...},  # 2. the opening value of every premise
    admission_rule=rule,  # 3. optional; none accepts every write
    termination_predicate=done,  # 4. optional; none lets silence close the run
    limits=RunLimits(...),  # 5. the two limits
    board=SqliteBoard(...),  # where the record is kept
)
```

The board has no default, because a run that keeps its record nowhere a second process can read is not a shared solution model. [Storage](storage.md) covers what to pass. The clock is injected the same way and defaults to the system clock, so a test substitutes one without changing what the model is.

`premises` must name each declared premise exactly once. Those writes bypass admission, and they wake nobody, because no agent has registered yet.

## Agents arrive by registering

No agent is named at creation. An agent comes to exist by registering, which is the only call that carries its callback, and a creator cannot know in advance which agents will join.

Registering wakes the agent immediately, covering every subscribed region that already holds something, because a newly registered agent is out of date with the whole board.

## Ending

A run does not close because nothing is outstanding at some instant. Agents are idle between notifications and premise at different times, so a quiet instant is the gap before the work rather than the end of it. Sustained silence is what ends it.

| Outcome | Cause |
| --- | --- |
| `Settled` | Nothing happened for the idle limit |
| `WallClockExpired` | The wall clock limit passed |
| `Aborted` | A caller closed the run |

Each carries `unfinished`, naming the agents still holding an unacknowledged notification. Why a run ended and which agents failed to finish are separate facts, so a run settles normally while one agent never returns. That distinction matters to a consumer, because a region nobody examined and a region examined with nothing in it are different states.

## Time is the only bound

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

Nothing counts writes or notifications. A write is the cause of a notification and a notification is its effect, and limiting the effect would mean that past some count a change lands and nobody is told, while the run stays open and still accepts writes. A record whose changes reach nobody has stopped being shared, and it would stop silently.

So the rule is absolute: **a change that lands is always told to every agent that should hear it.**

## After closing

Reads and the audit keep working. Writes, premise writes, and registrations are refused.
