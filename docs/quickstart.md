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

# 2. Each agent was woken as the run opened.
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

## What each step means

**Regions** are the named parts of the board. A `Level` holds what the agents worked out; a `Premise` holds something the work was given. [The board](concepts/board.md) describes what each holds and what a write to each one does.

**The store** is where the record is kept, and **the board id** says which board inside it this run is. One store holds many boards, so a service builds one store and creates many boards in it. [Storage](concepts/storage.md) covers the choice of store.

**The opening premises** give every declared premise its first value. They must name each one exactly once.

**The agents** are named when the run is created, so the run is ready to work the moment it exists. An agent that joins a run already under way calls `register_agent` instead.

**The notification** carries no values. It says the agent is out of date, and the agent reads the board itself.

**Acknowledging** says the agent has stopped working on that notification. It does not mean the agent found anything.
