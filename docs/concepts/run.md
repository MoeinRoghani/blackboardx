# The run

A model is one run: one board, one set of agents, from creation to close.

| Stage | What happens | Section |
| --- | --- | --- |
| Creation | The regions are declared, the premises take their opening values, the agents are named | [Creating a model](#creating-a-model) |
| Under way | Agents write, are notified, and acknowledge | [How agents join](#how-agents-join), [Acknowledging](#acknowledging) |
| Close | One of three outcomes is recorded, once | [Ending](#ending) |

What decides during the run is [the control component](control.md). What the
run writes to is [the board](board.md), and where that is kept is
[storage](storage.md).

## One create, many operations, one outcome

There is no opening and no handle to keep. The whole surface is three things.

| | What it is | How many |
| --- | --- | --- |
| **create** | `create_model` | Once per board. It converges, so a process does not have to know whether it is first. |
| **operations** | `write`, `set_premise`, the four reads, `ack` | As many as happen. Any replica serves any of them. |
| **close** | The outcome row | Once. Not a ceremony, just a write, from the check on access, the reaper, or `abort`. |

`Model` and `Control` bind a board identifier and the callables so a
caller does not repeat them. They are convenience over those operations, not a
connection and not a lease: see [the board is a handle](service.md#serving-a-board).

## Creating a model

Six things make a model. Two more say which board the run opens and where the record is kept.

```python
model = create_model(
    board_id="incident-3391",  # which board in the store
    store=SqliteStore(path),  # where the record is kept
    regions=[Level("signals"), Premise("severity")],  # 1. the named parts of the board
    premises={"severity": "high"},  # 2. the opening value of every premise
    agents=[Agent(name="triage", notify=deliver)],  # 3. the agents it starts with
    admission_rule=rule,  # 4. optional; none accepts every write
    termination_predicate=done,  # 5. optional; none lets silence close the run
    limits=RunLimits(  # 6. the two limits
        wall_clock=timedelta(minutes=30), idle=timedelta(seconds=30)
    ),
)
```

Every argument is keyword-only.

The store has no default, because a run that keeps its record nowhere that a second process can read is not a shared solution model. [Storage](storage.md) covers what to pass. The clock is injected the same way and defaults to the system clock, so a test substitutes another clock without changing what the model is.

Everything that the arguments alone settle is checked before the store is touched: each region being named once, the opening premises being exactly the declared premises, each opening value being JSON, the roster naming each agent once, and every region an agent subscribes to or writes to being declared. A call that raises has written nothing, so the corrected call opens the board it was going to open.

`premises` must name each declared premise exactly once. Those writes bypass admission, and they wake nobody, because no agent has registered yet.

`create_model` converges on the board. A region the record already holds with the same name and kind is recorded rather than written again, and an opening premise value is written only where the record holds none, so a process does not have to know whether it is the first. [Running as a service](service.md#losing-a-replica) covers the deployment that relies on it.

## How agents join

The creator names the agents the run starts with, and they are registered once the premises hold their opening values. Each agent is woken with one notification covering every subscribed region that already holds something, because an agent that has just joined is out of date with the whole board.

Naming the agents at creation is what makes the run ready the moment it exists. Nothing has to find the run and announce itself before work can start, so the wall clock does not run down while agents are still being discovered.

A run that is created with no agents named arms its wall clock all the same, and starts with nobody in it. Every second spent registering agents afterwards is a second of that run's wall clock: an agent registered twenty-nine minutes into a thirty minute run has one minute to work in.

An agent that joins a run already under way registers itself instead.

```python
model.control.register_agent(Agent(name="netops", notify=deliver))
```

That agent is woken the same way, so it hears about everything written before it arrived.

Both doors leave the same row on the run: the name, what wakes it, what it may write to, and where it is reached. They differ in when and not in what, so which agents a write should wake is read from the run and a replica that never saw an agent declared still records that it is owed a notification.

Registering a name that is already registered replaces that agent, which is how an agent that restarted or moved rejoins. [Write an agent](../guides/writing-an-agent.md#coming-back-after-a-restart) covers what survives.

## Acknowledging

An agent acknowledges a notification when the agent has stopped working on it. The acknowledgment says nothing about what the agent found. The run takes the agent's name as a keyword:

```python
model.control.ack(notification.notification_id, agent="triage")
```

An agent body takes an `AgentBoard` instead and calls `board.ack(notification.notification_id)`, without repeating its own name. `Control.as_agent(name)` returns an `AgentBoard` in process, and `BoardClient` is an `AgentBoard` over HTTP.

Acknowledging one notification acknowledges every notification to that agent whose range ends at or before the acknowledged notification's range. An agent that reads to the end of the board answers every range it was sent, by acknowledging the last one. Ranges are compared by where they end rather than by when they arrived, so acknowledging an earlier, narrower range leaves a later, wider one outstanding and the run keeps waiting for it.

A notification the agent never received cannot be acknowledged by name. A callback that raises is contained, so the agent does not learn what that notification covered or its identifier. Without the rule that an acknowledgment covers every earlier range, that notification would stay outstanding for the rest of the run and name a working agent unfinished.

Acknowledging a notification that is no longer outstanding changes nothing. Acknowledging a notification that was never issued to that agent raises `UnknownNotificationError`.

## Ending

A run does not close because nothing is outstanding at some instant. Agents are idle between notifications and they register at different times, so a quiet instant is the gap before the work rather than the end of it. A run ends when the quiet lasts long enough.

| Outcome | Cause |
| --- | --- |
| `Settled` | Nothing happened for the idle limit |
| `WallClockExpired` | The wall clock limit passed |
| `Aborted` | A caller closed the run |

An unacknowledged notification does not hold a run open. A run closes on silence with acknowledgments outstanding.

`Settled` and `WallClockExpired` carry `unfinished`, naming the agents still holding an unacknowledged notification. Why a run ended and which agents failed to finish are separate facts, so a run settles normally even though one agent never returns. `Aborted` carries an empty `unfinished`, whatever was outstanding when the caller closed the run. [End a run](../guides/ending-a-run.md) covers what to do with the outcome.

## Time is the only bound

```python
RunLimits(wall_clock=timedelta(hours=1), idle=timedelta(minutes=10))
```

Nothing counts writes or notifications. A count of notifications would limit the effect of a write rather than the write itself, so past that count a change would land and no agent would be told, while the run stayed open and kept accepting writes. A record whose changes reach nobody would stop being shared without saying so.

## After closing

Reads keep working, so the result stays available.

The write that records the outcome also removes, in the same transaction, what
only an open run needed: how far each agent had been told and had answered,
and any notification a write recorded that nothing had sent. Nothing reads
either once a run has ended, and the unfinished set the outcome carries was
computed from the first of them. What is left is the board and one row saying
how the run ended. An acknowledgment arriving after that changes nothing and
reports nothing, which is what it would have changed had the row still been
there.

A write to a level or a premise comes back `Rejected` with the cause `RUN_CLOSED`, because a write racing the close is ordinary and a caller has to handle it. Registering an agent or declaring a region raises `RunClosedError` instead, because those calls race nothing: a caller that makes one after the run has closed has made a mistake.
