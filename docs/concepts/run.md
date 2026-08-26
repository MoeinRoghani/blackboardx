# The run

A model is one run. It opens when it is created, and it ends in one of three states.

## Creating one

Six things configure a model, and a seventh says where the record is kept.

```python
model = create_model(
    regions=[...],  # 1. the named parts of the board
    premises={...},  # 2. the opening value of every premise
    agents=[...],  # 3. the agents the run starts with
    admission_rule=rule,  # 4. optional; none accepts every write
    termination_predicate=done,  # 5. optional; none lets silence close the run
    limits=RunLimits(...),  # 6. the two limits
    board=SqliteBoard(...),  # where the record is kept
)
```

The board has no default, because a run that keeps its record nowhere a second process can read is not a shared solution model. [Storage](storage.md) covers what to pass. The clock is injected the same way and defaults to the system clock, so a test substitutes one without changing what the model is.

`premises` must name each declared premise exactly once. Those writes bypass admission, and they wake nobody, because no agent has registered yet.

## How agents join

The creator names the agents the run starts with, and they are registered once the premises hold their opening values. Each one is woken immediately, covering every subscribed region that already holds something, because an agent that has just joined is out of date with the whole board.

Naming them at creation is what makes the run ready the moment it exists. Nothing has to find the run and announce itself before work can start, so the wall clock does not run down while agents are still being discovered.

An agent that joins a run already under way registers itself instead.

```python
model.control.register_agent(Agent(name="netops", notify=deliver))
```

It is woken the same way, so it hears about everything written before it arrived.

## Ending

A run does not close because nothing is outstanding at some instant. Agents are idle between notifications and they register at different times, so a quiet instant is the gap before the work rather than the end of it. A run ends when the quiet lasts long enough.

| Outcome | Cause |
| --- | --- |
| `Settled` | Nothing happened for the idle limit |
| `WallClockExpired` | The wall clock limit passed |
| `Aborted` | A caller closed the run |

Each carries `unfinished`, naming the agents still holding an unacknowledged notification. Why a run ended and which agents failed to finish are separate facts, so a run settles normally while one agent never returns. [End a run](../guides/ending-a-run.md) covers what to do with the outcome.

## Time is the only bound

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

Nothing counts writes or notifications. A count of notifications would limit the effect of a write rather than the write itself, so past that count a change would land and no agent would be told, while the run stayed open and kept accepting writes. A record whose changes reach nobody has stopped being shared, and it would stop without saying so.

## After closing

Reads and the audit keep working, so the result stays available.

A write to either kind of region comes back `Rejected` with the cause `RUN_CLOSED`, because a write racing the close is ordinary and a caller has to handle it. Registering an agent or declaring a region raises `RunClosedError` instead, because neither races anything: a caller that does either after the run has closed has made a mistake.
