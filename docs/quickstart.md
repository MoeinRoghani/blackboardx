# Quickstart

A run in full: create a model, premise an agent, let it contribute, and read the result.

```python
from datetime import timedelta

from blackboard import (
    Agent,
    Level,
    Premise,
    RunLimits,
    Settled,
    SqliteBoard,
    create_model,
)

notifications = []

# 1. Declare the regions and give every premise its opening value.
model = create_model(
    regions=[Level("platform"), Premise("window")],
    premises={"window": ["2026-08-16T20:00", "2026-08-16T22:00"]},
    limits=RunLimits(wall_clock=timedelta(minutes=10), idle=timedelta(seconds=1)),
    board=SqliteBoard("incident.sqlite3"),
)

# 2. An agent registers itself. Registering wakes it.
model.control.register_agent(Agent(name="ocp", notify=notifications.append))

# 3. The agent's cycle: read the premises, contribute, acknowledge.
(notification,) = notifications
window = model.reader.read_premise("window").value
model.control.write("ocp", "platform", {"window": window, "findings": ["oom"]})
model.control.ack("ocp", notification.notification_id)

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

## What each step means

**Regions** are the named parts of the board. A `Level` accumulates contributions; a `Premise` holds one current value. [The board](concepts/board.md) explains why there are exactly two kinds.

**The board** is where the record is kept. It is a required argument, because a run has to write somewhere a reader can find it. `SqliteBoard` suits one machine; a deployment passes an adapter for its own database. [Storage](concepts/storage.md) covers the choice.

**The opening premises** give every declared premise its first value. They must name each one exactly once.

**Registering** is how an agent comes to exist. Nothing names agents at creation, because an agent supplies its own callback and a creator cannot know which agents will join.

**The notification** carries no values. It says the agent is out of date, and the agent reads the board itself.

**Acknowledging** says the agent has stopped working on that notification. It does not mean the agent found anything.
