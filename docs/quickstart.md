# Quickstart

A run in full: create a model naming its agent, let the agent contribute, and read the result.

```python
from datetime import timedelta

from blackboard import (
    Agent,
    Level,
    Premise,
    RunLimits,
    Settled,
    SqliteStore,
    create_model,
)

notifications = []

# 1. Declare the regions, open the premises, and name the agents.
model = create_model(
    board_id="incident-4471",
    store=SqliteStore("incidents.sqlite3"),
    regions=[Level("platform"), Premise("window")],
    premises={"window": ["2026-08-16T20:00", "2026-08-16T22:00"]},
    agents=[Agent(name="ocp", notify=notifications.append)],
    limits=RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
)

# 2. Opening "window" was a write, and it woke the agent subscribed to it.
# 3. The agent's cycle: read the premises, contribute, acknowledge.
(notification,) = notifications
window = model.reader.read_premise("window").value
model.control.write("platform", {"window": window, "findings": ["oom"]}, writer="ocp")
model.control.ack(notification.notification_id, agent="ocp")

# 4. The run closes once nothing has happened for the idle limit.
assert model.control.wait_closed(timeout=timedelta(seconds=10)) == Settled()

for contribution in model.reader.read_level("platform"):
    print(contribution.sequence, contribution.content)
```

Printing:

```
2 {'window': ['2026-08-16T20:00', '2026-08-16T22:00'], 'findings': ['oom']}
```

The contribution has sequence 2 because the opening premise write took sequence 1. Every write to any region takes the next number from one counter.

The example leaves `incidents.sqlite3` in the working directory. Running it a second time raises `DuplicateRegionError`, because `create_model` opens a board the store does not hold yet. `attach_model` takes the same arguments without `premises`, whose values the record already holds, and opens a run over the board that is there:

```python
from blackboard import attach_model

model = attach_model(
    board_id="incident-4471",
    store=SqliteStore("incidents.sqlite3"),
    regions=[Level("platform"), Premise("window")],
    limits=RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
)
```

## What each step means

**Regions** are the named parts of the board. A `Level` holds what the agents worked out; a `Premise` holds something the work was given. [The board](concepts/board.md) describes what each holds and what a write to each one does.

**The store** is where the record is kept, and **the board id** says which board inside it this run is. One store holds many boards, so a service builds one store and creates many boards in it. [Storage](concepts/storage.md) covers the choice of store.

**The opening premises** give every declared premise its first value, and giving one is a write that reaches the board and takes a sequence number. They name exactly the declared premises: one left out, or one no region declares, raises `PremiseError`.

**The agents** are named when the run is created, so the run is ready to work the moment it exists. An agent that joins a run already under way calls `register_agent` instead.

**The subscription** was left unnamed here, so `ocp` subscribes to every premise and to no level. That is why opening `window` woke it and the later write to `platform` did not. An agent that names `subscribes_to` is woken by the regions it names and by no others.

**The notification** carries no values. It says the agent is out of date, and the agent reads the board itself.

**The writer** names who made the write, and `Control.write` takes it because the application here is writing on the agent's behalf. An agent that reads and writes for itself takes an `AgentBoard` instead, the same reads and writes with its own name already fixed: `model.control.as_agent("ocp")` returns one in this process, and `BoardClient` is one over HTTP. [Write an agent](guides/writing-an-agent.md) covers that.

**Acknowledging** says the agent has stopped working on that notification. It does not mean the agent found anything. It also clears every earlier notification to that agent whose sequence range ends no later than this one's.
